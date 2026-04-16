"""
Domain Exploration — build a mini-index from high-trust and unknown domains.

Trust-guided behaviour:
  ┌──────────────────┬───────────────────────────────────────┐
  │ Trust tier       │ Behaviour                             │
  ├──────────────────┼───────────────────────────────────────┤
  │ High  (≥ 0.75)   │ Crawl up to DEPTH_HIGH   pages (5)    │
  │ Medium (≥ 0.55)  │ Crawl up to DEPTH_MEDIUM pages (3)    │
  │ Low   (< 0.55)   │ Crawl up to DEPTH_LOW    pages (1)    │
  └──────────────────┴───────────────────────────────────────┘

When a CrawlBudget is provided:
  - Trust-based pool: domains sorted by trust (highest first); per-domain
    page count scales with trust tier; exploration halts on budget exhaust.
  - Exploration pool: a reserved slice of budget targets unknown and
    low-trust domains (trust < 0.5) RANDOMLY to break echo chambers.
    These results are tagged is_exploration=True for blending injection.

When no CrawlBudget is provided (backward-compatible):
  - Falls back to MAX_DOMAINS / MAX_PAGES_PER_DOMAIN constants.
"""
import asyncio
import json
from typing import List, Dict, Optional
from urllib.parse import urlparse, urljoin

import httpx
import trafilatura

from crawler.link_filter import filter_and_score_links
from cache.page_cache import get_cache

# ── Fallback limits (used when no budget is provided) ────────────────────────
SCORE_THRESHOLD      = 0.60
MAX_DOMAINS          = 3
MAX_PAGES_PER_DOMAIN = 4
FETCH_TIMEOUT        = 8.0
MIN_WORDS            = 120
MAX_INTERNAL_LINKS   = 80

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _base_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lstrip("www.")
    except Exception:
        return ""


def _extract_links_from_html(html: str, base_url: str) -> List[str]:
    """Parse raw HTML and return absolute internal links."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        links = []
        base_domain = _domain(base_url)
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith("#"):
                continue
            abs_url = urljoin(base_url, href)
            if _domain(abs_url) == base_domain:
                links.append(abs_url)
        return links
    except Exception:
        return []


async def _fetch_page(
    url: str,
    domain: str,
    semaphore: asyncio.Semaphore,
) -> Optional[Dict]:
    """Fetch and extract one page. Returns a result dict or None."""
    async with semaphore:
        try:
            async with httpx.AsyncClient(
                verify=False, timeout=FETCH_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=_HEADERS)
                if resp.status_code != 200:
                    return None
                html = resp.text
        except Exception:
            return None

    try:
        raw = trafilatura.extract(
            html,
            url=url,
            output_format="json",
            with_metadata=True,
            include_tables=True,
            favor_recall=True,
            no_fallback=False,
        )
        if raw:
            data = json.loads(raw)
            content = data.get("text") or ""
        else:
            content = trafilatura.extract(html, favor_recall=True) or ""
            data = {}
    except Exception:
        content = ""
        data = {}

    if not content:
        return None

    wc = len(content.split())
    if wc < MIN_WORDS:
        return None

    return {
        "url":           url,
        "domain":        domain,
        "title":         (data.get("title") or url.split("/")[-1])[:512],
        "snippet":       content[:300],
        "content":       content,
        "word_count":    wc,
        "publish_date":  data.get("date"),
        "author":        data.get("author"),
        "source":        "domain_explored",
        "discovered_via": f"domain_explore:{domain}",
        "from_cache":    False,
    }


async def _explore_domain(
    seed_url: str,
    known_urls: set,
    semaphore: asyncio.Semaphore,
    max_pages: int,
) -> List[Dict]:
    """
    Crawl a domain from seed_url, fetching up to max_pages internal pages.
    max_pages is determined by the caller (trust tier or budget allocation).
    """
    if max_pages <= 0:
        return []

    domain = _domain(seed_url)
    base   = _base_url(seed_url)

    # Fetch seed HTML to extract internal links
    try:
        async with httpx.AsyncClient(
            verify=False, timeout=FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(seed_url, headers=_HEADERS)
            html = resp.text if resp.status_code == 200 else ""
    except Exception:
        return []

    if not html:
        return []

    # Score internal links; skip already-known URLs
    raw_links   = _extract_links_from_html(html, base)
    fresh_links = [l for l in raw_links if l not in known_urls]

    scored = filter_and_score_links(
        fresh_links[:MAX_INTERNAL_LINKS],
        source_domain=domain,
        cross_domain_only=False,
        min_score=0.35,
    )

    fetch_targets = [
        c["url"] for c in scored
        if c["url"] != seed_url and c["url"] not in known_urls
    ][:max_pages]

    if not fetch_targets:
        return []

    tasks  = [_fetch_page(url, domain, semaphore) for url in fetch_targets]
    raw    = await asyncio.gather(*tasks, return_exceptions=True)
    return [p for p in raw if p and not isinstance(p, Exception)]


async def explore_high_score_domains(
    ranked_results: List[Dict],
    existing_urls: Optional[set] = None,
    budget=None,          # Optional[CrawlBudget]
) -> List[Dict]:
    """
    Explore high-scoring domains from `ranked_results`, crawling their
    internal pages for content search engines can't see.

    With a CrawlBudget:
      - Domains are sorted by trust (highest first).
      - Per-domain page count scales with trust tier.
      - Low-trust domains (trust < 0.35) are skipped.
      - Exploration halts when global budget is exhausted.

    Without a CrawlBudget:
      - Falls back to SCORE_THRESHOLD / MAX_DOMAINS / MAX_PAGES_PER_DOMAIN.

    Args:
        ranked_results: Scored results (need "scores.final" and "url").
        existing_urls:  URLs already in the pipeline — do not revisit.
        budget:         CrawlBudget instance (optional).

    Returns:
        New result dicts with source="domain_explored".
    """
    if existing_urls is None:
        existing_urls = set()

    # ── Collect seed candidates from current results ───────────────────────
    seed_map: Dict[str, str] = {}   # domain → best seed URL
    for r in ranked_results:
        s = r.get("scores")
        score = s.get("final", 0.0) if isinstance(s, dict) else 0.0
        if score >= SCORE_THRESHOLD:
            d = _domain(r.get("url", ""))
            if d and d not in seed_map:
                seed_map[d] = r["url"]

    # Supplement with cache's explorable domains (good score, not recently crawled)
    cache = get_cache()
    try:
        cached_domains = await cache.get_explorable_domains(
            min_score=SCORE_THRESHOLD, days_since=3, limit=MAX_DOMAINS * 2
        )
        for cd in cached_domains:
            d = cd.get("domain", "")
            if d and d not in seed_map:
                seed_map[d] = f"https://{d}/"
    except Exception:
        pass

    if not seed_map:
        return []

    # ── Sort domains by trust (highest first) and filter low-trust ────────
    if budget:
        # Filter out domains the budget won't allow at all
        domains_sorted = [
            d for d in budget.sort_domains(list(seed_map.keys()))
            if budget.depth_limit(d) > 0
        ]
    else:
        domains_sorted = list(seed_map.keys())[:MAX_DOMAINS]

    candidates = [(d, seed_map[d]) for d in domains_sorted[:MAX_DOMAINS]]

    semaphore      = asyncio.Semaphore(3)
    all_new_pages: List[Dict] = []

    # ── Trust-based exploration (main loop) ───────────────────────────────────
    for domain, seed_url in candidates:
        if budget:
            alloc = budget.allocate(domain, DEPTH_HIGH_FOR_EXPLORER)
            if alloc == 0:
                continue   # budget exhausted or domain limit hit
            max_pages = alloc
        else:
            max_pages = MAX_PAGES_PER_DOMAIN

        try:
            pages = await _explore_domain(seed_url, existing_urls, semaphore, max_pages)
            all_new_pages.extend(pages)
            for p in pages:
                existing_urls.add(p.get("url", ""))
            if pages:
                await cache.mark_domain_explored(domain, len(pages))
        except Exception as exc:
            print(f"[DomainExplorer] {domain}: {exc}")

    # ── Exploration-pool sampling (Phase 5 — anti-echo-chamber) ──────────────
    # Build candidate list from outbound links in ranked_results, then let
    # budget.get_exploration_candidates() randomly pick unknown/low-trust ones.
    if budget:
        link_candidates: List[Dict] = []
        for r in ranked_results:
            for link in (r.get("_outbound_links") or []):
                url = link if isinstance(link, str) else link.get("url", "")
                if url:
                    link_candidates.append({"url": url})

        expl_targets = budget.get_exploration_candidates(link_candidates, max_n=4)

        for c in expl_targets:
            url    = c.get("url", "")
            domain = _domain(url)
            if not domain or url in existing_urls:
                continue
            alloc = budget.allocate_exploration(domain, want=1)
            if alloc == 0:
                continue
            try:
                pages = await _explore_domain(url, existing_urls, semaphore, max_pages=1)
                for p in pages:
                    p["is_exploration"] = True   # tag for blending injection
                    existing_urls.add(p.get("url", ""))
                all_new_pages.extend(pages)
            except Exception as exc:
                print(f"[DomainExplorer] explore-pool {domain}: {exc}")

    return all_new_pages


# Upper bound used for budget.allocate — the budget itself caps the actual depth
DEPTH_HIGH_FOR_EXPLORER = 5
