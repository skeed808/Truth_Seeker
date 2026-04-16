"""
Micro-Crawler — Shallow Depth Expansion

For the top N seed results by score, fetches the page, extracts internal
links, then fetches 1 level deeper. This surfaces content that search engines
would never return directly (deep-linked articles, sub-pages, etc.)

Flow:
  1. Take top MAX_SEEDS already-ranked results as seeds
  2. For each seed, fetch HTML and extract internal links (same domain)
  3. Score and filter links (skip nav/tag/category pages)
  4. Fetch up to MAX_PER_SEED links per seed (async, bounded by semaphore)
  5. Extract content from each discovered page
  6. Mark each result with discovered_via = seed URL
  7. Return list of new result dicts (same shape as scraper output)

Hard limits to keep latency acceptable:
  MAX_SEEDS      = 5   (seeds to crawl from)
  MAX_PER_SEED   = 3   (internal links to follow per seed)
  MAX_TOTAL      = 12  (absolute cap on new pages)
  FETCH_TIMEOUT  = 7s  (per page)
"""
import asyncio
import re
import json
from typing import List, Dict, Optional, Set
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
import tldextract

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

try:
    import trafilatura
    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _TRAFILATURA_AVAILABLE = False

MAX_SEEDS     = 5
MAX_PER_SEED  = 3
MAX_TOTAL     = 12
FETCH_TIMEOUT = 7.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Path segments that indicate nav/utility pages (not content)
_SKIP_PATH_PATTERNS = re.compile(
    r"/(tag|tags|category|categories|author|authors|page|pages"
    r"|login|logout|signup|register|search|cart|checkout"
    r"|wp-admin|wp-content|feed|rss|sitemap"
    r"|cdn-cgi|static|assets|images|img|css|js)/",
    re.IGNORECASE,
)
_SKIP_EXTENSIONS = re.compile(
    r"\.(xml|rss|json|jpg|jpeg|png|gif|svg|pdf|zip|tar|gz|mp4|mp3|webp|ico)$",
    re.IGNORECASE,
)


def _normalize_url(url: str) -> str:
    """Strip fragment and normalise for deduplication."""
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))
    except Exception:
        return url


def _extract_internal_links(html: str, base_url: str) -> List[str]:
    """
    Parse HTML and return internal links (same domain, plausible content paths).
    Falls back to regex if BeautifulSoup unavailable.
    """
    base_parsed = urlparse(base_url)
    base_domain = base_parsed.netloc.lower()

    if _BS4_AVAILABLE:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        raw_hrefs = [tag.get("href", "") for tag in soup.find_all("a", href=True)]
    else:
        # Regex fallback
        raw_hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)

    links: List[str] = []
    seen: Set[str] = set()

    for href in raw_hrefs:
        href = href.strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue

        full = urljoin(base_url, href)
        parsed = urlparse(full)

        # Must be same domain, http/https
        if parsed.netloc.lower() != base_domain:
            continue
        if parsed.scheme not in ("http", "https"):
            continue

        path = parsed.path.lower()

        # Skip nav/utility paths
        if _SKIP_PATH_PATTERNS.search(path):
            continue
        if _SKIP_EXTENSIONS.search(path):
            continue

        # Skip root / very short paths (home, about, contact)
        path_parts = [p for p in path.split("/") if p]
        if len(path_parts) < 1:
            continue

        normalised = _normalize_url(full)
        parent_normalised = _normalize_url(base_url)

        if normalised != parent_normalised and normalised not in seen:
            seen.add(normalised)
            links.append(full)

    return links[:20]   # cap candidates before async fetch


async def _fetch_html(url: str, client: httpx.AsyncClient) -> Optional[str]:
    try:
        resp = await client.get(url, headers=_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
        if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
            return resp.text
    except Exception:
        pass
    return None


def _extract_content(html: str, url: str) -> Dict:
    """Extract text + metadata from HTML using trafilatura (or fallback)."""
    if _TRAFILATURA_AVAILABLE:
        try:
            raw_json = trafilatura.extract(
                html, url=url, include_comments=False, include_tables=True,
                no_fallback=False, favor_recall=True,
                output_format="json", with_metadata=True,
            )
            if raw_json:
                data = json.loads(raw_json)
                content = data.get("text") or ""
                return {
                    "content": content,
                    "word_count": len(content.split()),
                    "author": data.get("author"),
                    "publish_date": data.get("date"),
                }
        except Exception:
            pass
        plain = trafilatura.extract(html, favor_recall=True) or ""
        return {"content": plain, "word_count": len(plain.split()), "author": None, "publish_date": None}

    # Minimal regex fallback when trafilatura absent
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s{2,}", " ", text).strip()[:8000]
    return {"content": text, "word_count": len(text.split()), "author": None, "publish_date": None}


async def crawl_deeper(
    seed_results: List[Dict],
    budget=None,    # Optional[CrawlBudget]
) -> List[Dict]:
    """
    Crawl one level deeper from top seeds and return new discovered pages.

    With a CrawlBudget:
      - Seeds are sorted by domain trust (highest-trust first).
      - Per-seed page limit = budget.allocate(domain, MAX_PER_SEED).
        High-trust seeds may get more pages; low-trust seeds may get none.
      - Total pages are capped by the shared budget.

    Without a budget:
      - Original behaviour: MAX_SEEDS seeds, MAX_PER_SEED pages each,
        MAX_TOTAL total cap.

    Each returned result includes:
      • All standard fields (title, url, snippet, domain, source, content)
      • discovered_via: URL of the seed page it was found on
    """
    if not seed_results:
        return []

    # Sort seeds by trust (highest first) when budget is provided
    if budget:
        pool = sorted(
            seed_results[:MAX_SEEDS * 2],
            key=lambda r: budget.trust(r.get("domain", "")),
            reverse=True,
        )
        # Pre-allocate budget per seed; skip seeds with zero allocation
        seed_allocs: List[tuple] = []  # (seed_dict, alloc)
        for seed in pool:
            domain = seed.get("domain", "")
            alloc  = budget.allocate(domain, MAX_PER_SEED)
            if alloc > 0:
                seed_allocs.append((seed, alloc))
            if len(seed_allocs) >= MAX_SEEDS:
                break
    else:
        seed_allocs = [(s, MAX_PER_SEED) for s in seed_results[:MAX_SEEDS]]

    if not seed_allocs:
        return []

    discovered:    List[Dict] = []
    total_fetched: int        = 0
    semaphore = asyncio.Semaphore(4)

    async def process_seed(seed: Dict, per_seed_limit: int):
        nonlocal total_fetched
        seed_url = seed.get("url", "")
        if not seed_url:
            return

        async with semaphore:
            async with httpx.AsyncClient(verify=False, timeout=FETCH_TIMEOUT + 2) as client:
                seed_html = await _fetch_html(seed_url, client)

        if not seed_html:
            return

        candidates = _extract_internal_links(seed_html, seed_url)

        fetched_for_seed = 0
        for link_url in candidates:
            if total_fetched >= MAX_TOTAL or fetched_for_seed >= per_seed_limit:
                break

            ext    = tldextract.extract(link_url)
            domain = f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain

            async with semaphore:
                async with httpx.AsyncClient(verify=False, timeout=FETCH_TIMEOUT) as client:
                    page_html = await _fetch_html(link_url, client)

            if not page_html:
                continue

            meta = _extract_content(page_html, link_url)
            if meta["word_count"] < 100:
                continue

            title_match = re.search(
                r"<title[^>]*>([^<]{3,200})</title>", page_html, re.IGNORECASE
            )
            title = title_match.group(1).strip() if title_match else link_url

            discovered.append({
                "title":        title,
                "url":          link_url,
                "snippet":      " ".join(meta["content"].split()[:40]) + "…"
                                if meta["content"] else "",
                "domain":       domain,
                "source":       "crawled",
                "discovered_via": seed_url,
                **meta,
            })
            fetched_for_seed += 1
            total_fetched    += 1

    await asyncio.gather(
        *[process_seed(s, alloc) for s, alloc in seed_allocs],
        return_exceptions=True,
    )
    return discovered
