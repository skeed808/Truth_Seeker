"""
Advanced Obscurity Model v2

Combines five independent signals into a single [0,1] score.
Higher score = more obscure / independent / niche.

Signals:
  1. Domain type — mainstream penalty, forum/indie boost
  2. TLD category — .edu/.org/.io boost; .com/.biz penalty
  3. URL depth — deeper paths tend to be more specific/obscure
  4. Domain rarity in result set — rare domain = less indexed
  5. Cross-source penalisation — appearing in both DDG + Brave = well-indexed

Design note: signals 1-3 are per-result; 4-5 require the full result list.
call score_obscurity(result, all_results) always.
"""
import re
from typing import Dict, List
from urllib.parse import urlparse

import tldextract

# ── Hard-coded mainstream domains ────────────────────────────────────────────
# Domains that are too well-known to be "obscure" regardless of any other signal
MAINSTREAM_DOMAINS = {
    "wikipedia.org", "amazon.com", "google.com", "youtube.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "reddit.com", "quora.com", "pinterest.com", "ebay.com",
    "walmart.com", "bestbuy.com", "target.com", "apple.com",
    "microsoft.com", "netflix.com", "spotify.com", "medium.com",
    "forbes.com", "businessinsider.com", "techcrunch.com", "wired.com",
    "cnn.com", "bbc.com", "bbc.co.uk", "nytimes.com", "wsj.com",
    "theguardian.com", "huffpost.com", "buzzfeed.com", "mashable.com",
    "engadget.com", "theverge.com", "gizmodo.com", "pcmag.com",
    "cnet.com", "zdnet.com", "tomsguide.com", "tomshardware.com",
    "ign.com", "gamespot.com", "kotaku.com", "polygon.com",
    "healthline.com", "webmd.com", "mayoclinic.org", "nih.gov",
    "imdb.com", "rottentomatoes.com", "goodreads.com", "tripadvisor.com",
}

# ── Domain type boosts ────────────────────────────────────────────────────────
# Substrings in domain name that indicate community / personal / indie sources
FORUM_COMMUNITY_SIGNALS = [
    "forum", "forums", "discuss", "community", "board", "bbs",
    "talk", "stackexchange", "overflow", "answers", "tildes",
    "lemmy", "lobste", "mastodon", "tilde", "sdf.org", "circumlunar",
    "news.ycombinator",
]

PERSONAL_SITE_SIGNALS = [
    "blog", "diary", "notes", "journal", "writings", "musings",
    "substack", "bearblog", "neocities", "nekoweb",
]

# ── TLD scoring ───────────────────────────────────────────────────────────────
TLD_WEIGHTS = {
    "edu": +0.10,
    "gov": +0.06,
    "org": +0.05,
    "io":  +0.05,
    "xyz": +0.04,   # often indie/experimental
    "net": +0.00,
    "co":  -0.03,
    "com": -0.05,
    "info": -0.03,
    "biz": -0.08,
    "shop": -0.10,
    "store": -0.10,
}


def _url_depth_score(url: str) -> float:
    """
    Score based on URL path depth.
    / = 0.0, /about = 0.15, /blog/2023/post-title = 0.6, /a/b/c/d/e = 1.0
    Rationale: deep paths indicate specific content, not home/category pages.
    """
    try:
        path = urlparse(url).path
        # Count meaningful path segments (ignore empty strings from leading/trailing /)
        segments = [s for s in path.split("/") if s and s not in ("index.html", "index.php")]
        # Asymptotic: 4+ segments → score near 1.0
        depth = len(segments)
        return min(1.0 - (1.0 / (1.0 + depth * 0.4)), 0.90)
    except Exception:
        return 0.0


def _domain_rarity_score(domain: str, all_results: List[Dict]) -> float:
    """
    Domains that appear many times in the result set are better-indexed
    (more mainstream). Penalise repeats.

    1 occurrence  → 1.00  (very rare / niche)
    2 occurrences → 0.80
    3 occurrences → 0.55
    4 occurrences → 0.35
    5+            → 0.15
    """
    count = sum(1 for r in all_results if r.get("domain", "").lower() == domain)
    mapping = {1: 1.00, 2: 0.80, 3: 0.55, 4: 0.35}
    return mapping.get(count, 0.15)


def _cross_source_penalty(result: Dict, all_results: List[Dict]) -> float:
    """
    If the same URL (or very similar domain) appears in both DDG AND Brave
    results it is well-indexed = less obscure.
    Returns a penalty [0, 0.15] to subtract.
    """
    url = result.get("url", "")
    domain = result.get("domain", "").lower()
    sources = {r.get("source") for r in all_results if r.get("domain", "").lower() == domain}
    if len(sources) >= 2:
        return 0.12   # appeared in multiple scrapers
    return 0.0


def score_obscurity(result: Dict, all_results: List[Dict]) -> float:
    """
    Unified obscurity score combining domain type, TLD, URL depth, rarity,
    and cross-source signal.

    Returns [0.0, 1.0] — higher = more obscure / independent.
    """
    domain = result.get("domain", "").lower().strip()
    url = result.get("url", "")

    # ── Hard penalties for known mainstream sites ─────────────────────────────
    if domain in MAINSTREAM_DOMAINS:
        return 0.04
    for ms in MAINSTREAM_DOMAINS:
        if domain.endswith("." + ms):
            return 0.08

    # ── Signal 1: domain type classification ─────────────────────────────────
    type_score = 0.45  # baseline for unknown/generic domain

    if any(s in domain for s in FORUM_COMMUNITY_SIGNALS):
        type_score = 0.80   # forums are inherently obscure/niche
    elif any(s in domain for s in PERSONAL_SITE_SIGNALS):
        type_score = 0.70   # personal blogs
    else:
        # Personal site heuristics
        ext = tldextract.extract(url)
        core = ext.domain.lower()
        if "-" in core:               # hyphenated = often personal/project
            type_score += 0.08
        if ext.subdomain and ext.subdomain not in ("www", "blog", "en", "m", "mobile", "api"):
            type_score += 0.06        # non-trivial subdomain = project/personal

    # ── Signal 2: TLD category ────────────────────────────────────────────────
    ext = tldextract.extract(url)
    suffix_parts = ext.suffix.split(".") if ext.suffix else ["com"]
    tld = suffix_parts[-1].lower()
    tld_score = TLD_WEIGHTS.get(tld, 0.0)

    # Country-code TLDs (2-char, not .co/.uk which are generic) = slight boost
    if len(tld) == 2 and tld not in ("co", "uk", "us"):
        tld_score += 0.04

    # ── Signal 3: URL depth ───────────────────────────────────────────────────
    depth_score = _url_depth_score(url)

    # ── Signal 4: Domain rarity in result set ────────────────────────────────
    rarity_score = _domain_rarity_score(domain, all_results)

    # ── Signal 5: Cross-source penalty ───────────────────────────────────────
    cross_penalty = _cross_source_penalty(result, all_results)

    # ── Weighted combination ──────────────────────────────────────────────────
    # type_score is the dominant signal; others refine it
    raw = (
        type_score   * 0.40
        + tld_score  * 0.10   # already an additive delta, fine as contribution
        + depth_score * 0.15
        + rarity_score * 0.35
        - cross_penalty
    )

    return float(max(0.0, min(1.0, raw)))
