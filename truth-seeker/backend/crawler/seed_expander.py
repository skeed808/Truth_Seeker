"""
Seed Expansion Engine — widen the result pool via outbound link following.

Trust-guided behaviour:
  - Candidate links are sorted by (link_quality_score × trust_multiplier)
    so links pointing at known-good domains float to the top.
  - When a CrawlBudget is provided, low-trust targets are filtered out
    before any network calls are made.
  - Budget is consumed via pre-allocation (one slot per domain) so the
    shared budget accurately reflects resources spent by all crawlers.

Without a CrawlBudget, falls back to the original MAX_TOTAL_FETCH cap.
"""
import asyncio
from typing import List, Dict, Optional
from urllib.parse import urlparse

import httpx
import trafilatura

from crawler.link_filter import filter_and_score_links

# ── Fallback limits (no budget) ───────────────────────────────────────────────
MAX_LINKS_PER_RESULT = 40
MAX_CANDIDATES       = 60
MAX_TOTAL_FETCH      = 8
FETCH_TIMEOUT        = 7.0
MIN_LINK_SCORE       = 0.40
MIN_WORDS_EXPANDED   = 100

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lstrip("www.")
    except Exception:
        return ""


def _collect_candidates(
    results: List[Dict],
    existing_domains: set,
    budget=None,          # Optional[CrawlBudget]
) -> List[Dict]:
    """
    Gather cross-domain link candidates from results' _outbound_links,
    score them with the URL quality filter, then sort by trust × quality.

    With a budget:
      - Candidates from domains below the skip threshold are removed.
      - Remaining candidates are sorted by trust_multiplier × link_score.

    Without a budget:
      - Sorted by raw link_score only.
    """
    all_links: List[str] = []
    for r in results:
        raw = r.get("_outbound_links") or []
        all_links.extend(raw[:MAX_LINKS_PER_RESULT])

    if not all_links:
        return []

    scored = filter_and_score_links(
        all_links,
        cross_domain_only=True,
        min_score=MIN_LINK_SCORE,
    )

    # Drop domains already in the result set
    filtered = [
        c for c in scored
        if _domain(c["url"]) not in existing_domains
    ][:MAX_CANDIDATES]

    if budget:
        # Remove low-trust candidates and sort by trust × link quality
        filtered = budget.filter_feasible(filtered)
        filtered = budget.sort_candidates(filtered)
    # (Without budget, filter_and_score_links already sorted by quality desc)

    return filtered


async def _fetch_and_extract(
    url: str,
    semaphore: asyncio.Semaphore,
) -> Optional[Dict]:
    """Fetch one URL and extract content via trafilatura."""
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
        import json as _json
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
            data = _json.loads(raw)
            content = data.get("text") or ""
        else:
            content = trafilatura.extract(html, favor_recall=True) or ""
            data = {}
    except Exception:
        content = ""
        data = {}

    if not content or len(content.split()) < MIN_WORDS_EXPANDED:
        return None

    domain = _domain(url)
    return {
        "url":           url,
        "domain":        domain,
        "title":         (data.get("title") or url.split("/")[-1])[:512],
        "snippet":       content[:300],
        "content":       content,
        "word_count":    len(content.split()),
        "publish_date":  data.get("date"),
        "author":        data.get("author"),
        "source":        "seed_expanded",
        "discovered_via": "seed_expand",
        "from_cache":    False,
    }


async def expand_from_seeds(
    enriched_results: List[Dict],
    max_fetches: int = MAX_TOTAL_FETCH,
    budget=None,          # Optional[CrawlBudget]
) -> List[Dict]:
    """
    Extract outbound links from enriched results and fetch the best ones.

    With a CrawlBudget:
      - Candidates are trust-sorted (high-trust targets first).
      - Per-domain slot is pre-allocated from the budget before fetching.
      - Domains below the skip threshold are never fetched.

    Without a budget:
      - Up to max_fetches distinct domains are fetched (original behaviour).

    Args:
        enriched_results: Results from extract_content_batch (with _outbound_links).
        max_fetches:      Fallback cap when no budget is provided.
        budget:           Shared CrawlBudget (optional).

    Returns:
        New result dicts with source="seed_expanded".
    """
    if not enriched_results:
        return []

    existing_domains = {_domain(r.get("url", "")) for r in enriched_results}
    existing_urls    = {r.get("url", "") for r in enriched_results}

    candidates = _collect_candidates(enriched_results, existing_domains, budget=budget)
    if not candidates:
        return []

    # Build fetch queue — one slot per new domain, respect budget
    fetch_queue: List[str] = []
    seen_domains: set = set()

    for c in candidates:
        url    = c["url"]
        domain = _domain(url)

        if url in existing_urls or domain in seen_domains:
            continue

        if budget:
            # Pre-allocate one page from the budget for this domain
            alloc = budget.allocate(domain, 1)
            if alloc == 0:
                continue    # domain's budget is full or trust too low
        else:
            if len(fetch_queue) >= max_fetches:
                break

        seen_domains.add(domain)
        fetch_queue.append(url)

        if budget is None and len(fetch_queue) >= max_fetches:
            break

    semaphore = asyncio.Semaphore(4)
    results: List[Dict] = []

    # ── Fetch trust-sorted candidates ─────────────────────────────────────────
    if fetch_queue:
        tasks = [_fetch_and_extract(url, semaphore) for url in fetch_queue]
        raw   = await asyncio.gather(*tasks, return_exceptions=True)
        results.extend(item for item in raw if item and not isinstance(item, Exception))

    # ── Exploration-pool sampling (Phase 5 — anti-echo-chamber) ──────────────
    # Randomly sample unknown/low-trust domains from outbound links and fetch
    # 1 page each via the exploration pool.  Results are tagged is_exploration.
    if budget:
        link_candidates: List[Dict] = []
        for r in enriched_results:
            for link in (r.get("_outbound_links") or []):
                url = link if isinstance(link, str) else link.get("url", "")
                if url:
                    link_candidates.append({"url": url})

        expl_targets = budget.get_exploration_candidates(link_candidates, max_n=3)
        expl_queue: List[str] = []

        for c in expl_targets:
            url    = c.get("url", "")
            domain = _domain(url)
            if not domain or url in existing_urls or domain in seen_domains:
                continue
            alloc = budget.allocate_exploration(domain, want=1)
            if alloc == 0:
                continue
            seen_domains.add(domain)
            expl_queue.append(url)

        if expl_queue:
            expl_tasks = [_fetch_and_extract(url, semaphore) for url in expl_queue]
            expl_raw   = await asyncio.gather(*expl_tasks, return_exceptions=True)
            for item in expl_raw:
                if item and not isinstance(item, Exception):
                    item["is_exploration"] = True   # tag for blending injection
                    results.append(item)

    return results
