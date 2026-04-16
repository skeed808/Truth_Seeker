"""
Deduplication utilities.
Two-pass dedup:
  1. Exact URL normalization (strip www, trailing slash, fragments)
  2. Fuzzy title similarity (Jaccard word overlap > 0.75 threshold)
"""
import re
from typing import List, Dict
from urllib.parse import urlparse, urlunparse


def _normalize_url(url: str) -> str:
    """Canonical form of a URL for exact-match dedup."""
    try:
        p = urlparse(url.lower().strip())
        netloc = re.sub(r"^www\.", "", p.netloc)
        path = p.path.rstrip("/")
        # Drop fragment; keep query string (different queries = different pages)
        return urlunparse(("", netloc, path, "", p.query, ""))
    except Exception:
        return url.lower().strip()


def _jaccard(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(re.sub(r"[^a-z0-9 ]", "", a.lower()).split())
    words_b = set(re.sub(r"[^a-z0-9 ]", "", b.lower()).split())
    # Strip stop words to reduce false negatives on common words
    stops = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it",
              "for", "on", "with", "at", "by", "from", "this", "that"}
    words_a -= stops
    words_b -= stops
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def deduplicate_results(results: List[Dict], title_threshold: float = 0.75) -> List[Dict]:
    """
    Remove duplicate results.
    DDG and Brave often return the same URLs; fuzzy title check catches
    canonical vs. non-canonical URL pairs for the same article.
    """
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    deduped: List[Dict] = []

    for result in results:
        url_key = _normalize_url(result.get("url", ""))
        title = result.get("title", "")

        if url_key in seen_urls:
            continue

        # Fuzzy title dedup — skip if we already have a very similar title
        is_dup = any(_jaccard(title, t) >= title_threshold for t in seen_titles)
        if is_dup:
            continue

        seen_urls.add(url_key)
        seen_titles.append(title)
        deduped.append(result)

    return deduped
