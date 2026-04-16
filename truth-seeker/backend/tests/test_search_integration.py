"""
Integration tests — POST /api/search pipeline wiring.

What's real:
  classify_query_intent, variants_for_scraping, deduplicate_results,
  rank_results, blend_for_diversity, _apply_filters, _trust_distribution,
  CrawlBudget

What's mocked (all external I/O):
  fetch_ddg_results, fetch_brave_results, extract_content_batch,
  wayback_fallback_batch, explore_high_score_domains, cache (all methods)

Goal: assert that Phase-5 wiring is correct end-to-end:
  - exploration_results_count > 0 when domain_explore=True and the crawler
    returns is_exploration-tagged results
  - exploration_results_count == 0 when no crawl flags are set
  - intent field is classified and returned
  - trust_distribution shape is correct and sums to total
"""
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from main import app


# ── Fake data factories ───────────────────────────────────────────────────────

def _scraper_result(i: int) -> dict:
    """Simulate what fetch_ddg_results / fetch_brave_results returns."""
    return {
        "url":          f"https://site{i}.example.com/article-{i}",
        "domain":       f"site{i}.example.com",
        "title":        f"Article {i}: a detailed guide to software engineering practices",
        "snippet":      f"This detailed snippet for result {i} covers several important topics in depth.",
        "source":       "ddg" if i % 2 == 0 else "brave",
        "word_count":   600 + i * 40,
        "publish_date": "2024-03-01",
        "from_cache":   False,
    }


def _enriched_result(i: int) -> dict:
    """
    Simulate extract_content_batch output.
    Adds content (needed by rank_results scorers) and _outbound_links
    (needed by crawlers — included because domain_explore=True sets include_links=True).
    """
    r = _scraper_result(i)
    r["content"]          = ("word " * 200) + f" unique content for result {i}"
    r["word_count"]       = 220
    r["_outbound_links"]  = [f"https://linked-from-{i}.example.com/page"]
    return r


def _exploration_result() -> dict:
    """
    Simulate explore_high_score_domains returning an anti-echo-chamber page.
    is_exploration=True is the critical field this test asserts on.
    """
    return {
        "url":           "https://obscure-independent.example.net/deep-analysis",
        "domain":        "obscure-independent.example.net",
        "title":         "An independent deep-dive analysis of software practices",
        "snippet":       "Independent research from an unknown domain, injected to break echo chambers.",
        "content":       "word " * 180 + " independent analysis unique content",
        "source":        "domain_explored",
        "discovered_via": "domain_explore:obscure-independent.example.net",
        "word_count":    200,
        "publish_date":  None,
        "from_cache":    False,
        "is_exploration": True,
    }


def _make_cache_mock() -> MagicMock:
    """Return a MagicMock cache where all async methods are AsyncMocks."""
    cache = MagicMock()
    cache.get_domain_trust_map = AsyncMock(return_value={})
    cache.search               = AsyncMock(return_value=[])
    cache.store_batch          = AsyncMock(return_value=None)
    cache.stats                = AsyncMock(return_value={"total_pages": 42})
    return cache


# ── Base patch set shared across tests ───────────────────────────────────────
# All external I/O is patched at the routes.search import level.

_BASE_PATCHES = {
    "routes.search.fetch_ddg_results":   lambda: AsyncMock(
        return_value=[_scraper_result(i) for i in range(4)]
    ),
    "routes.search.fetch_brave_results": lambda: AsyncMock(
        return_value=[_scraper_result(i) for i in range(4, 7)]
    ),
    "routes.search.extract_content_batch": lambda: AsyncMock(
        return_value=[_enriched_result(i) for i in range(7)]
    ),
    "routes.search.wayback_fallback_batch": lambda: AsyncMock(
        side_effect=lambda results: results
    ),
}


def _apply_base_patches(stack, cache_mock, **overrides):
    """Enter base patches + any overrides into an ExitStack."""
    from contextlib import ExitStack
    patches = {**_BASE_PATCHES, **overrides}
    for target, factory in patches.items():
        stack.enter_context(patch(target, new=factory()))
    stack.enter_context(patch("routes.search.get_cache", return_value=cache_mock))


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestExplorationWiring:

    def test_exploration_results_count_nonzero_when_domain_explore_true(self):
        """
        Core wiring test: explore_high_score_domains returning an
        is_exploration-tagged result must produce exploration_results_count > 0.
        """
        from contextlib import ExitStack
        cache = _make_cache_mock()

        with ExitStack() as stack:
            _apply_base_patches(
                stack,
                cache,
                **{"routes.search.explore_high_score_domains":
                    lambda: AsyncMock(return_value=[_exploration_result()])},
            )
            client = TestClient(app)
            resp = client.post("/api/search", json={
                "query":          "software engineering practices",
                "domain_explore": True,
                "seed_expand":    False,
                "deep_crawl":     False,
                "use_cache":      True,
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["exploration_results_count"] > 0, (
            f"exploration_results_count should be > 0 when domain_explore=True "
            f"and crawler returns is_exploration-tagged results. "
            f"Got: {data['exploration_results_count']}"
        )

    def test_exploration_results_count_zero_without_any_crawl_flags(self):
        """
        Without domain_explore / seed_expand / deep_crawl, no crawlers run →
        no exploration results → count must be 0.
        """
        from contextlib import ExitStack
        cache = _make_cache_mock()

        with ExitStack() as stack:
            _apply_base_patches(stack, cache)
            client = TestClient(app)
            resp = client.post("/api/search", json={
                "query":          "software engineering practices",
                "domain_explore": False,
                "seed_expand":    False,
                "deep_crawl":     False,
                "use_cache":      True,
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["exploration_results_count"] == 0

    def test_exploration_result_appears_in_results_list(self):
        """
        The individual result tagged is_exploration=True must appear
        somewhere in the returned results list.
        """
        from contextlib import ExitStack
        cache = _make_cache_mock()
        expl = _exploration_result()

        with ExitStack() as stack:
            _apply_base_patches(
                stack,
                cache,
                **{"routes.search.explore_high_score_domains":
                    lambda: AsyncMock(return_value=[expl])},
            )
            client = TestClient(app)
            resp = client.post("/api/search", json={
                "query":          "software engineering practices",
                "domain_explore": True,
                "use_cache":      True,
                "max_results":    50,
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        expl_results = [r for r in data["results"] if r.get("is_exploration")]
        assert len(expl_results) >= 1, (
            "Expected at least one is_exploration=True result in the results list"
        )


class TestIntentField:

    def test_deep_research_intent_classified_and_returned(self):
        """'why' question → deep_research intent in response."""
        from contextlib import ExitStack
        cache = _make_cache_mock()

        with ExitStack() as stack:
            _apply_base_patches(stack, cache)
            client = TestClient(app)
            resp = client.post("/api/search", json={
                "query": "why does inflation cause economic recessions",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "intent" in data
        assert data["intent"] == "deep_research"

    def test_freshness_sensitive_intent_for_recent_year(self):
        """Query with a year in 2023-2039 → freshness_sensitive."""
        from contextlib import ExitStack
        cache = _make_cache_mock()

        with ExitStack() as stack:
            _apply_base_patches(stack, cache)
            client = TestClient(app)
            resp = client.post("/api/search", json={
                "query": "best programming languages 2024",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "freshness_sensitive"

    def test_informational_intent_not_hidden(self):
        """Short generic query → informational intent is returned (not suppressed)."""
        from contextlib import ExitStack
        cache = _make_cache_mock()

        with ExitStack() as stack:
            _apply_base_patches(stack, cache)
            client = TestClient(app)
            resp = client.post("/api/search", json={"query": "machine learning"})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["intent"] == "informational"


class TestTrustDistribution:

    def test_trust_distribution_has_correct_shape(self):
        """Response must include trust_distribution with high/medium/low keys."""
        from contextlib import ExitStack
        cache = _make_cache_mock()

        with ExitStack() as stack:
            _apply_base_patches(stack, cache)
            client = TestClient(app)
            resp = client.post("/api/search", json={"query": "open source tools"})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "trust_distribution" in data
        td = data["trust_distribution"]
        assert set(td.keys()) == {"high", "medium", "low"}, (
            f"Expected keys high/medium/low, got {set(td.keys())}"
        )

    def test_trust_distribution_sums_to_total(self):
        """high + medium + low must equal total results returned."""
        from contextlib import ExitStack
        cache = _make_cache_mock()

        with ExitStack() as stack:
            _apply_base_patches(stack, cache)
            client = TestClient(app)
            resp = client.post("/api/search", json={
                "query":       "open source tools",
                "max_results": 50,
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        td = data["trust_distribution"]
        assert td["high"] + td["medium"] + td["low"] == data["total"], (
            f"trust_distribution sum {td} != total {data['total']}"
        )
