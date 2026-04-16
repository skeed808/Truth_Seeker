"""
Result Clustering

Assigns a content-type cluster label to each result using domain signals,
URL structure, and lightweight content heuristics.

Labels:
  Forum      — discussion threads, Q&A, community boards
  Blog       — personal/independent long-form writing
  Academic   — papers, preprints, university / research content
  Commercial — e-commerce, product pages, review sites with affiliate content
  News       — news publications and journalism
  Wiki       — Wikipedia-style collaborative encyclopedias
  Docs       — developer docs, technical references, man pages
  Unknown    — can't confidently classify
"""
import re
from typing import Dict

# ── Known domain → label mappings ────────────────────────────────────────────
_KNOWN_FORUM_DOMAINS = {
    "reddit.com", "news.ycombinator.com", "lobste.rs", "tildes.net",
    "stackexchange.com", "stackoverflow.com", "superuser.com", "serverfault.com",
    "askubuntu.com", "mathoverflow.net", "physicsforums.com", "quora.com",
    "lemmy.world", "kbin.social", "beehaw.org", "slashdot.org",
    "4chan.org", "voat.co",
}

_KNOWN_ACADEMIC_DOMAINS = {
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "scholar.google.com",
    "semanticscholar.org", "researchgate.net", "jstor.org", "ssrn.com",
    "biorxiv.org", "medrxiv.org", "plos.org", "springer.com", "nature.com",
    "sciencedirect.com", "ieee.org", "acm.org", "dl.acm.org",
}

_KNOWN_NEWS_DOMAINS = {
    "bbc.com", "bbc.co.uk", "cnn.com", "reuters.com", "ap.org",
    "nytimes.com", "washingtonpost.com", "theguardian.com", "wsj.com",
    "ft.com", "bloomberg.com", "npr.org", "theatlantic.com",
    "newyorker.com", "economist.com", "politico.com", "axios.com",
    "techcrunch.com", "wired.com", "arstechnica.com",
}

_KNOWN_DOCS_DOMAINS = {
    "docs.python.org", "developer.mozilla.org", "man7.org", "linux.die.net",
    "docs.rs", "pkg.go.dev", "docs.microsoft.com", "learn.microsoft.com",
    "cppreference.com", "devdocs.io", "readthedocs.io",
}

# ── URL path patterns ─────────────────────────────────────────────────────────
_FORUM_URL_PATTERNS = [
    r"/forum/", r"/forums/", r"/thread/", r"/threads/",
    r"/discuss/", r"/board/", r"/boards/", r"/topic/",
    r"/questions?/", r"/answers?/", r"/post/", r"/posts/",
    r"/t/\d", r"/r/[a-z]", r"/comments/",
]

_ACADEMIC_URL_PATTERNS = [
    r"/papers?/", r"/publications?/", r"/preprint/", r"/pdf/",
    r"/abstract/", r"/doi/", r"\.edu/", r"/proceedings/",
    r"/journal/", r"/article/", r"/research/",
]

_DOCS_URL_PATTERNS = [
    r"/docs/", r"/documentation/", r"/reference/", r"/api/",
    r"/man/", r"/manual/", r"/guide/", r"/handbook/",
    r"/spec/", r"/rfc", r"/wiki/",
]

_NEWS_URL_PATTERNS = [
    r"/news/", r"/article/", r"/story/", r"\d{4}/\d{2}/\d{2}/",
    r"/politics/", r"/world/", r"/tech/", r"/science/",
]

# ── Content text signals ──────────────────────────────────────────────────────
_ACADEMIC_TEXT_SIGNALS = [
    r"\babstract\b", r"\bkeywords?\b", r"\breferences?\b",
    r"\bcited?\s+by\b", r"\bet\s+al\b", r"\bdoi\s*:", r"\bissn\b",
    r"\bpeer.?reviewed\b", r"\bjournal\b", r"\bpreprint\b",
    r"\bin\s+this\s+(paper|study|experiment|analysis)\b",
]

_COMMERCIAL_TEXT_SIGNALS = [
    r"\badd\s+to\s+cart\b", r"\bbuy\s+now\b", r"\$\s*\d",
    r"\bpromo\s+code\b", r"\bcoupon\b", r"\bdiscount\b",
    r"\bfree\s+shipping\b", r"\bour\s+#?1\s+pick\b",
]

_BLOG_TEXT_SIGNALS = [
    r"\bposted\s+by\b", r"\bwritten\s+by\b", r"\bauthor\b",
    r"\bcomments?\s*\(\d+\)", r"\bsubscribe\b", r"\bnewsletter\b",
    r"\bpodcast\b", r"\blast\s+updated\b",
]


def _count_url_pattern_hits(url: str, patterns: list) -> int:
    url_lower = url.lower()
    return sum(1 for p in patterns if re.search(p, url_lower))


def _count_text_signals(text: str, patterns: list) -> int:
    text_lower = text.lower()[:2000]   # only scan first 2k chars for speed
    return sum(1 for p in patterns if re.search(p, text_lower))


def classify_result(result: Dict) -> str:
    """
    Return a cluster label string for a result dict.
    Decision logic: known-domain lookup → URL pattern → content text → fallback.
    """
    domain = result.get("domain", "").lower()
    url    = result.get("url", "").lower()
    text   = (result.get("content") or result.get("snippet", "") or "")

    # ── 1. Hard-coded known domains ───────────────────────────────────────────
    if "wikipedia" in domain or "wikimedia" in domain:
        return "Wiki"
    if domain in _KNOWN_FORUM_DOMAINS:
        return "Forum"
    if domain in _KNOWN_ACADEMIC_DOMAINS or ".edu" in domain or ".ac." in domain:
        return "Academic"
    if domain in _KNOWN_NEWS_DOMAINS:
        return "News"
    if domain in _KNOWN_DOCS_DOMAINS or "readthedocs" in domain:
        return "Docs"

    # ── 2. URL path pattern scoring ───────────────────────────────────────────
    scores = {
        "Forum":    _count_url_pattern_hits(url, _FORUM_URL_PATTERNS),
        "Academic": _count_url_pattern_hits(url, _ACADEMIC_URL_PATTERNS),
        "Docs":     _count_url_pattern_hits(url, _DOCS_URL_PATTERNS),
        "News":     _count_url_pattern_hits(url, _NEWS_URL_PATTERNS),
    }

    # ── 3. Content text scoring (if available) ────────────────────────────────
    if text:
        scores["Academic"]   += _count_text_signals(text, _ACADEMIC_TEXT_SIGNALS) * 2
        scores["Commercial"] =  _count_text_signals(text, _COMMERCIAL_TEXT_SIGNALS) * 2
        scores["Blog"]       =  _count_text_signals(text, _BLOG_TEXT_SIGNALS)

    # ── 4. Domain name heuristics ─────────────────────────────────────────────
    if any(s in domain for s in ("forum", "discuss", "board", "community", "overflow")):
        scores["Forum"] = scores.get("Forum", 0) + 3
    if any(s in domain for s in ("blog", "journal", "diary", "notes", "writings", "substack")):
        scores.setdefault("Blog", 0)
        scores["Blog"] += 2
    if any(s in domain for s in ("shop", "store", "buy", "price", "deals", "review")):
        scores.setdefault("Commercial", 0)
        scores["Commercial"] += 3
    if any(s in domain for s in ("news", "times", "post", "herald", "gazette", "daily")):
        scores["News"] = scores.get("News", 0) + 2

    # ── 5. Author presence = likely blog ─────────────────────────────────────
    if result.get("author"):
        scores.setdefault("Blog", 0)
        scores["Blog"] += 1

    # ── 6. Existing commercial_bias score shortcut ────────────────────────────
    existing_commercial = (result.get("scores") or {}).get("commercial_bias", 0)
    if existing_commercial > 0.5:
        scores.setdefault("Commercial", 0)
        scores["Commercial"] += 3

    # ── Pick winner ───────────────────────────────────────────────────────────
    if not scores or max(scores.values(), default=0) == 0:
        return "Unknown"

    winner = max(scores, key=scores.get)
    # Require at least 1 point to avoid noise
    if scores[winner] == 0:
        return "Unknown"

    return winner
