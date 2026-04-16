"""
Link Quality Filter — score URLs without fetching them.

Assigns a [0.0, 1.0] quality estimate to any URL based purely on its
structure.  Used by the seed expander to decide which outbound links are
worth fetching and by the domain explorer to pick the most promising
internal pages to visit.

Scoring components (all normalised to [0,1] before weighting):
  url_depth      (0.20) — moderate depth suggests real content, not nav
  slug_quality   (0.35) — readable, word-rich slugs beat numeric IDs
  date_bonus     (0.10) — /YYYY/ date in path = probably content page
  tld_score      (0.15) — .edu / .gov / .org preferred over .com / .io
  path_penalty   (0.20) — hard-reject nav/utility patterns → score → 0

Design decisions:
  - Pure function; zero I/O, zero imports outside stdlib
  - Called with thousands of URLs per search; must be O(1) per call
  - "Unknown quality" returns 0.50 — uncertain, not bad
"""
import re
from typing import Optional
from urllib.parse import urlparse

# ── Hard-skip patterns ────────────────────────────────────────────────────────
# URLs matching ANY of these get score 0.0 immediately.
_SKIP_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Navigation / chrome
    r"/tag/", r"/tags/", r"/category/", r"/categories/",
    r"/author/", r"/authors/", r"/profile/",
    r"/page/\d+/?$", r"/\?page=", r"[?&]p=\d+",
    r"/feed/?$", r"/rss/?$", r"\.xml$", r"\.rss$",
    r"/wp-content/", r"/wp-includes/", r"/wp-admin/",
    r"/cdn-cgi/", r"/static/", r"/assets/", r"/dist/",
    r"/(login|logout|signin|signup|register)/?$",
    r"/(privacy|terms|tos|legal|cookies|gdpr|ccpa)/?",
    r"/(cart|checkout|basket|wishlist|account|billing)/?",
    r"/(about|contact|advertise|subscribe)/?$",
    r"/sitemap", r"/robots\.txt$",
    # Media / downloads
    r"\.(jpg|jpeg|png|gif|webp|svg|ico|mp4|mp3|pdf|zip|gz|tar|exe|dmg)$",
    # Tracking / redirect noise
    r"[?&](utm_|ref=|affiliate|click_id|session)",
    r"/redirect/", r"/out/", r"/go/",
    # Pagination via query string
    r"[?&](offset|start)=\d+",
]]

# ── "Likely content" path segments  ──────────────────────────────────────────
_CONTENT_SEGMENTS = {
    "article", "articles", "post", "posts", "blog", "blogs",
    "paper", "papers", "research", "study", "wiki", "docs",
    "howto", "how-to", "tutorial", "guide", "analysis",
    "forum", "thread", "discussion", "question", "answer",
    "journal", "report", "essay", "review",
}

# ── Date pattern in path ──────────────────────────────────────────────────────
_DATE_IN_PATH = re.compile(r"/\d{4}/(?:\d{2}/)?(?:\d{2}/)?")

# ── Short, word-poor slug patterns ────────────────────────────────────────────
_NUMERIC_SLUG   = re.compile(r"^[\d\-]+$")          # pure numbers / dashes
_HEX_SLUG       = re.compile(r"^[a-f0-9\-]{8,}$")   # hex IDs
_SINGLE_CHAR    = re.compile(r"^[a-z\d]$")


def _slug_quality(path: str) -> float:
    """
    Score the URL path slug.  Returns [0, 1].
    High score = human-readable, word-rich slug.
    """
    if not path or path == "/":
        return 0.30   # root pages are usually nav, not content

    # Grab the deepest non-empty segment
    segments = [s for s in path.rstrip("/").split("/") if s]
    if not segments:
        return 0.30

    slug = segments[-1]

    # Hard-penalise numeric / hex IDs
    if _NUMERIC_SLUG.match(slug) or _HEX_SLUG.match(slug):
        return 0.10

    # Words in the slug (split on hyphens, underscores, dots)
    words = [w for w in re.split(r"[\-_\.]+", slug) if len(w) > 2]
    word_score = min(len(words) / 5.0, 1.0)        # 5+ words → 1.0

    # Bonus if a content segment appears in the path
    path_lower = path.lower()
    has_content_seg = any(seg in path_lower for seg in _CONTENT_SEGMENTS)

    return min(word_score * 0.70 + (0.30 if has_content_seg else 0.0), 1.0)


def _depth_score(path: str) -> float:
    """
    Path depth score.
    Depth 0 (root) → 0.2, Depth 1 → 0.5, Depth 2-3 → 0.9, Depth 4+ → 0.6.
    Very deep paths are usually pagination or CMS artefacts.
    """
    depth = len([s for s in path.rstrip("/").split("/") if s])
    if depth == 0:
        return 0.20
    if depth == 1:
        return 0.50
    if depth <= 3:
        return 0.90
    if depth <= 5:
        return 0.70
    return 0.40   # very deep


def _tld_score(hostname: str) -> float:
    """Prefer academic/gov TLDs; penalise commercial / exotic."""
    h = hostname.lower()
    if h.endswith(".edu") or h.endswith(".ac.uk") or ".edu." in h:
        return 1.0
    if h.endswith(".gov") or h.endswith(".mil"):
        return 0.90
    if h.endswith(".org"):
        return 0.75
    if h.endswith(".net"):
        return 0.65
    if h.endswith(".com"):
        return 0.55
    if h.endswith(".io") or h.endswith(".dev"):
        return 0.60
    # Country codes — neutral
    return 0.60


def score_url(url: str) -> float:
    """
    Return a quality estimate in [0.0, 1.0] for *url* without fetching it.
    Returns 0.0 for hard-skipped URLs and 0.50 for unparseable ones.
    """
    if not url or not url.startswith(("http://", "https://")):
        return 0.0

    # Hard skip
    for pat in _SKIP_PATTERNS:
        if pat.search(url):
            return 0.0

    try:
        parsed = urlparse(url)
    except Exception:
        return 0.50

    path     = parsed.path or "/"
    hostname = parsed.netloc or ""

    depth   = _depth_score(path)           # 0.20 weight
    slug    = _slug_quality(path)          # 0.35 weight
    date    = 0.10 if _DATE_IN_PATH.search(path) else 0.0   # fixed bonus
    tld     = _tld_score(hostname)         # 0.15 weight

    score = (depth * 0.20) + (slug * 0.35) + date + (tld * 0.15)

    # Remaining 0.20 is a base "not penalised" grant
    score += 0.20

    return round(min(max(score, 0.0), 1.0), 3)


def should_skip(url: str) -> bool:
    """
    Fast boolean gate — True means the URL should not be fetched at all.
    Equivalent to score_url(url) == 0.0 but short-circuits after first match.
    """
    if not url or not url.startswith(("http://", "https://")):
        return True
    for pat in _SKIP_PATTERNS:
        if pat.search(url):
            return True
    return False


def filter_and_score_links(
    links: list[str],
    source_domain: Optional[str] = None,
    cross_domain_only: bool = False,
    min_score: float = 0.30,
) -> list[dict]:
    """
    Filter a list of raw URLs and return scored, sorted dicts.

    Args:
        links:             Raw URLs extracted from a page.
        source_domain:     The domain the links were found on.
        cross_domain_only: If True, only return links to *other* domains.
        min_score:         Minimum quality threshold; lower scores are dropped.

    Returns:
        List of {"url": ..., "score": ..., "cross_domain": bool} sorted
        by score descending.
    """
    seen: set[str] = set()
    out: list[dict] = []

    for raw in links:
        url = raw.strip()
        if not url or url in seen:
            continue
        seen.add(url)

        if should_skip(url):
            continue

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lstrip("www.")
        except Exception:
            continue

        is_cross = (source_domain is None) or (domain != source_domain)

        if cross_domain_only and not is_cross:
            continue

        q = score_url(url)
        if q < min_score:
            continue

        out.append({"url": url, "score": q, "cross_domain": is_cross})

    out.sort(key=lambda x: x["score"], reverse=True)
    return out
