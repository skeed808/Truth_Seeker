"""
Search route v5 — intent-adaptive, anti-bias, exploration-aware pipeline.

Pipeline stages:
  1. Query intent classification (navigational / informational / deep_research / freshness_sensitive)
  2. Query expansion (3–5 variants via query_expander)
  3. Parallel scraping + cache lookup + domain-trust prefetch (all concurrent)
  4. Deduplication
  5. Content extraction (top 15 candidates, include_links for seed_expand)
  6. Wayback fallback for thin results
  7. Preliminary ranking (intent-adaptive weights + domain trust signal)
  8. Create shared CrawlBudget (intent-adaptive, trust-guided resource allocator)
  9. [Optional] Micro-crawl — trust-sorted seeds, depth scales with trust
  10. [Optional] Seed expansion — trust-weighted + exploration-pool sampling
  11. [Optional] Domain exploration — high-trust crawl + exploration-pool sampling
  12. Final ranking over all sources (intent, query, domain trust)
  13. Hard filters
  14. Fire-and-forget: selective cache store (quality-gated)
  15. Return with full observability payload

New in v5 (intent + exploration):
  - Query intent drives CrawlBudget exploration_ratio and depth_mult
  - Intent adjusts ranking weight vector (freshness/obscurity/diversity)
  - Exploration pool reserved budget targets unknown/low-trust domains (anti-echo-chamber)
  - Exploration results tagged is_exploration=True; blending layer injects ≥ 2 into top 10
  - Anti-gaming: title stuffing, near-duplicate fingerprinting, over-optimisation penalty
  - Observability: intent, exploration_used, exploration_results_count, trust_distribution
"""
import asyncio
from collections import Counter
from fastapi import APIRouter
from pydantic import BaseModel, Field

from scrapers.duckduckgo import fetch_ddg_results
from scrapers.brave import fetch_brave_results
from scrapers.reddit import fetch_reddit_results
from scrapers.wayback import wayback_fallback_batch
from utils.extractor import extract_content_batch
from utils.dedup import deduplicate_results
from utils.query_expander import variants_for_scraping
from ranking.engine import rank_results
from ranking.query_intent import classify_query_intent
from crawler.micro_crawler import crawl_deeper
from crawler.seed_expander import expand_from_seeds
from crawler.domain_explorer import explore_high_score_domains
from crawler.crawl_budget import CrawlBudget
from cache.page_cache import get_cache
from cache import query_memory as _qmem
from cache import user_feedback as _feedback

# Shared crawl budget — pages across ALL crawlers combined per request
_CRAWL_BUDGET    = 25
MAX_MICRO_SEEDS  = 7    # more seeds considered; budget + trust trim the real set

router = APIRouter()


# ── Debug / stats endpoint ────────────────────────────────────────────────────

@router.get("/debug/storage")
async def debug_storage():
    """Return DB health stats: page count, domain count, size, avg score."""
    cache = get_cache()
    return await cache.stats()


@router.post("/debug/cleanup")
async def debug_cleanup():
    """Manually trigger TTL expiry + size-cap trim. Returns deletion counts."""
    cache = get_cache()
    return await cache.cleanup()

# ── Filter sets ───────────────────────────────────────────────────────────────
CORPORATE_DOMAINS = {
    "amazon", "google", "youtube", "facebook", "twitter", "instagram",
    "linkedin", "tiktok", "netflix", "apple", "microsoft", "pinterest",
    "ebay", "walmart", "bestbuy", "target", "etsy", "shopify",
    "forbes", "businessinsider", "techcrunch", "wired", "cnet", "zdnet",
    "cnn", "bbc", "nytimes", "wsj", "theguardian", "huffpost", "buzzfeed",
    "mashable", "engadget", "theverge", "gizmodo", "pcmag", "tomsguide",
    "tomshardware", "ign", "gamespot", "kotaku", "polygon", "pcgamer",
}

FORUM_INDICATORS = {
    "domains": {
        "reddit.com", "news.ycombinator.com", "stackexchange.com",
        "stackoverflow.com", "lobste.rs", "tildes.net", "lemmy.world",
        "kbin.social", "disqus.com", "quora.com",
    },
    "substrings": [
        "forum", "discuss", "community", "board", "talk", "answers",
        "ask", "thread", "topic", "post", "/t/", "/r/", "/questions/",
    ],
}


# ── Request model ─────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    url:      str = Field(..., min_length=1, max_length=2000)
    query:    str = Field(..., min_length=1, max_length=500)
    feedback: int = Field(..., ge=-1, le=1)


class SearchPreferences(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)

    # ── Sliders ──────────────────────────────────────────────────────────────
    underground_bias: float = Field(0.5, ge=0.0, le=1.0)
    freshness_bias:   float = Field(0.5, ge=0.0, le=1.0)

    # ── Hard filters ─────────────────────────────────────────────────────────
    exclude_corporate: bool = False
    forums_only:       bool = False
    long_form_only:    bool = False

    # ── v2 toggles ────────────────────────────────────────────────────────────
    deep_crawl:       bool = False   # micro-crawler
    deseo_mode:       bool = False   # aggressive AI/SEO penalty
    forums_priority:  bool = False   # boost forum results

    # ── v3 toggles ────────────────────────────────────────────────────────────
    use_cache:        bool = True    # use + populate persistent cache
    seed_expand:      bool = False   # follow outbound links
    domain_explore:   bool = False   # crawl domain internals

    max_results: int = Field(20, ge=1, le=50)


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.post("/feedback", status_code=200)
async def feedback(req: FeedbackRequest):
    """Record a thumbs-up (+1) or thumbs-down (-1) vote for a result."""
    if req.feedback not in (1, -1):
        return {"ok": False, "error": "feedback must be 1 or -1"}
    asyncio.create_task(
        asyncio.to_thread(_feedback.store_feedback, req.url, req.query, req.feedback)
    )
    return {"ok": True}


@router.post("/search")
async def search(prefs: SearchPreferences):
    """Intent-adaptive exploration pipeline — v5."""

    cache = get_cache()

    # ── Stage 1: query intent classification ─────────────────────────────────
    intent = classify_query_intent(prefs.query)

    # ── Stage 2: query expansion ──────────────────────────────────────────────
    extra_variants = variants_for_scraping(prefs.query)   # up to 3 extras

    # ── Stage 3: parallel scraping + cache lookup ─────────────────────────────
    # Domain trust is fetched concurrently as a fire-and-start task so it is
    # ready by Stage 6 without adding any latency to the scraping phase.
    trust_task = (
        asyncio.create_task(cache.get_domain_trust_map())
        if prefs.use_cache else None
    )

    scraper_tasks = [
        asyncio.create_task(fetch_ddg_results(prefs.query)),
        asyncio.create_task(fetch_brave_results(prefs.query)),
    ]
    for variant in extra_variants:
        scraper_tasks.append(asyncio.create_task(fetch_ddg_results(variant)))

    if prefs.forums_priority or prefs.forums_only:
        scraper_tasks.append(
            asyncio.create_task(fetch_reddit_results(prefs.query, max_results=8))
        )

    cache_task = (
        asyncio.create_task(cache.search(prefs.query, limit=15))
        if prefs.use_cache else None
    )

    all_tasks     = scraper_tasks + ([cache_task] if cache_task else [])
    batch_results = await asyncio.gather(*all_tasks, return_exceptions=True)

    all_results: list = []
    cache_hits  = 0

    if cache_task:
        scrape_batches = batch_results[:-1]
        cache_batch    = batch_results[-1]
        if isinstance(cache_batch, list):
            cache_hits = len(cache_batch)
            all_results.extend(cache_batch)
    else:
        scrape_batches = batch_results

    for batch in scrape_batches:
        if isinstance(batch, list):
            all_results.extend(batch)

    if not all_results:
        return {
            "query": prefs.query, "total": 0, "results": [],
            "error": "No results from any source.",
        }

    # ── Stage 4: deduplication ────────────────────────────────────────────────
    deduped = deduplicate_results(all_results)

    # ── Stage 5: content extraction ───────────────────────────────────────────
    # include_links for seed_expand AND domain_explore (both need outbound links
    # to build their exploration candidate lists)
    include_links = prefs.seed_expand or prefs.domain_explore
    enriched = await extract_content_batch(
        deduped[:15],
        include_links=include_links,
    )
    if len(deduped) > 15:
        enriched.extend(deduped[15:])

    # ── Stage 6: Wayback fallback for thin pages ──────────────────────────────
    enriched = await wayback_fallback_batch(enriched)

    # ── Resolve domain trust (fast DB read; started in Stage 3) ──────────────
    domain_trust: dict = {}
    if trust_task:
        try:
            domain_trust = await trust_task
        except Exception:
            pass

    # ── Stage 7: preliminary ranking (intent-adaptive weights + trust) ─────────
    prelim_ranked = rank_results(
        [r.copy() for r in enriched],
        prefs,
        domain_trust=domain_trust,
        query=prefs.query,
        intent=intent,
    )

    # ── Create shared crawl budget (intent-adaptive, trust-guided) ────────────
    # All three crawlers share this budget; intent adjusts exploration_ratio
    # and depth_mult so deep_research casts wide, navigational stays focused.
    any_crawling = prefs.deep_crawl or prefs.seed_expand or prefs.domain_explore
    budget = CrawlBudget(
        total=_CRAWL_BUDGET,
        domain_trust=domain_trust,
        intent=intent,
    ) if any_crawling else None

    # ── Stage 8: optional micro-crawl (trust-sorted, budget-limited) ──────────
    crawled: list = []
    if prefs.deep_crawl:
        try:
            seeds    = prelim_ranked[:MAX_MICRO_SEEDS]
            seed_map = {r["url"]: r for r in enriched}
            for s in seeds:
                if s["url"] in seed_map:
                    s["content"] = seed_map[s["url"]].get("content")
            crawled = await crawl_deeper(seeds, budget=budget)
        except Exception as exc:
            print(f"[Crawler] micro_crawl error: {exc}")

    # ── Stage 9: optional seed expansion (trust-weighted + exploration pool) ───
    seed_expanded: list = []
    if prefs.seed_expand:
        try:
            seed_expanded = await expand_from_seeds(enriched, budget=budget)
        except Exception as exc:
            print(f"[SeedExpander] error: {exc}")

    # ── Stage 10: optional domain exploration (trust crawl + explore pool) ─────
    domain_explored: list = []
    if prefs.domain_explore:
        try:
            existing_urls = {r.get("url") for r in enriched + crawled + seed_expanded}
            domain_explored = await explore_high_score_domains(
                prelim_ranked, existing_urls=existing_urls, budget=budget
            )
        except Exception as exc:
            print(f"[DomainExplorer] error: {exc}")

    # ── Stage 11: final ranking over all sources ──────────────────────────────
    combined = deduplicate_results(enriched + crawled + seed_expanded + domain_explored)

    # Load feedback votes for all result URLs (sync DB read, fast)
    all_urls     = [r.get("url", "") for r in combined if r.get("url")]
    feedback_map = await asyncio.to_thread(_feedback.get_feedback_map, all_urls)

    final_ranked = rank_results(
        combined,
        prefs,
        domain_trust=domain_trust,
        query=prefs.query,
        intent=intent,
        feedback_map=feedback_map,
    )

    # ── Stage 12: hard filters ────────────────────────────────────────────────
    filtered = _apply_filters(final_ranked, prefs)

    # ── Stage 13: fire-and-forget cache store (non-blocking) ─────────────────
    if prefs.use_cache:
        asyncio.create_task(
            cache.store_batch(filtered, query=prefs.query)
        )

    # ── Stage 13b: log query to memory (fire-and-forget) ─────────────────────
    asyncio.create_task(
        asyncio.to_thread(_qmem.log_successful_query, prefs.query, intent)
    )

    # ── Stage 14: build observability payload ─────────────────────────────────
    saved_pages_total = 0
    if prefs.use_cache:
        try:
            st = await cache.stats()
            saved_pages_total = st.get("total_pages", 0)
        except Exception:
            pass

    exploration_results_count = sum(
        1 for r in filtered if r.get("is_exploration")
    )
    exploration_used = budget.explore_pool - budget._explore_remaining if budget else 0

    # Trust score distribution over the result set (low / medium / high)
    trust_distribution = _trust_distribution(filtered, domain_trust)

    return {
        # ── Core ──────────────────────────────────────────────────────────────
        "query":                    prefs.query,
        "total":                    len(filtered),
        "results":                  filtered[: prefs.max_results],
        # ── Observability (Phase 5) ───────────────────────────────────────────
        "intent":                   intent,
        "exploration_used":         exploration_used,
        "exploration_results_count":exploration_results_count,
        "trust_distribution":       trust_distribution,
        # ── Pipeline stats ────────────────────────────────────────────────────
        "sources_used":             _count_sources(combined),
        "deep_crawl_pages":         len(crawled),
        "seed_expanded_pages":      len(seed_expanded),
        "domain_explored_pages":    len(domain_explored),
        "cache_hits":               cache_hits,
        "saved_pages_total":        saved_pages_total,
        "query_variants":           len(extra_variants),
        "crawl_budget":             budget.summary() if budget else None,
        "trusted_domains":          len(domain_trust),
    }


# ── Filter helpers ────────────────────────────────────────────────────────────

def _apply_filters(results, prefs):
    out = results

    if prefs.exclude_corporate:
        out = [
            r for r in out
            if not any(corp in r.get("domain", "") for corp in CORPORATE_DOMAINS)
        ]

    if prefs.forums_only:
        def is_forum(r):
            domain = r.get("domain", "")
            url    = r.get("url", "")
            if domain in FORUM_INDICATORS["domains"]:
                return True
            return any(s in domain or s in url for s in FORUM_INDICATORS["substrings"])
        out = [r for r in out if is_forum(r)]

    if prefs.long_form_only:
        out = [r for r in out if r.get("word_count", 0) > 800]

    return out


def _count_sources(results):
    return dict(Counter(r.get("source", "unknown") for r in results))


def _trust_distribution(results: list, domain_trust: dict) -> dict:
    """
    Break result domains into three trust bands.

    Returns:
        {"high": N, "medium": N, "low": N}
        where high ≥ 0.75, medium ≥ 0.55, low = everything else.
    """
    high = medium = low = 0
    for r in results:
        t = domain_trust.get(r.get("domain", ""), 0.50)
        if t >= 0.75:
            high += 1
        elif t >= 0.55:
            medium += 1
        else:
            low += 1
    return {"high": high, "medium": medium, "low": low}
