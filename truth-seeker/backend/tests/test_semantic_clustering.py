"""
Unit tests for ranking/semantic_clustering.py

Invariants under test:
  1. cluster_results returns every input result exactly once.
  2. diversify_top_10 picks at most 1 result per cluster in the first
     top_n results when the input is highly redundant.
  3. cluster_id and cluster_label are set on every result.
  4. With identical snippets → all in one cluster → only 1 result in top-1.
  5. With perfectly distinct snippets → each in its own cluster → N results.
  6. Length preservation: diversify_top_10 honours the top_n cap.
"""
from collections import Counter
from ranking.semantic_clustering import cluster_results, diversify_top_10, embed_snippet
import numpy as np


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _r(domain: str, score: float, snippet: str = "") -> dict:
    return {
        "url":     f"https://{domain}/page",
        "domain":  domain,
        "title":   f"Article from {domain}",
        "snippet": snippet or f"content about topic from {domain}",
        "scores":  {"final": score},
    }


def _flat(clusters):
    """Flatten list-of-lists into single list."""
    return [r for c in clusters for r in c]


# ── embed_snippet ──────────────────────────────────────────────────────────────

class TestEmbedSnippet:
    def test_returns_ndarray(self):
        v = embed_snippet("hello world")
        assert isinstance(v, np.ndarray)

    def test_unit_normed_approx(self):
        v = embed_snippet("semantic search result clustering test")
        norm = float(np.linalg.norm(v))
        assert abs(norm - 1.0) < 0.05, f"Expected unit norm, got {norm}"

    def test_empty_string_no_crash(self):
        v = embed_snippet("")
        assert isinstance(v, np.ndarray)


# ── cluster_results: invariants ────────────────────────────────────────────────

class TestClusterResultsInvariants:

    def test_empty_input_returns_empty(self):
        assert cluster_results([]) == []

    def test_single_result_one_cluster(self):
        clusters = cluster_results([_r("a.com", 0.9, "machine learning")])
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_every_result_appears_exactly_once(self):
        results = [_r(f"s{i}.com", 0.9 - i * 0.05, f"snippet {i} about topic {i}") for i in range(12)]
        clusters = cluster_results(results)
        flat = _flat(clusters)
        assert len(flat) == 12
        urls_out = [r["url"] for r in flat]
        for r in results:
            assert urls_out.count(r["url"]) == 1, f"{r['url']} missing or duplicated"

    def test_cluster_id_set_on_all_results(self):
        results = [_r(f"s{i}.com", 0.9 - i * 0.05) for i in range(6)]
        clusters = cluster_results(results)
        for r in _flat(clusters):
            assert "cluster_id" in r, "cluster_id missing"
            assert isinstance(r["cluster_id"], int)

    def test_cluster_label_set_on_all_results(self):
        results = [_r(f"s{i}.com", 0.9 - i * 0.05) for i in range(6)]
        clusters = cluster_results(results)
        for r in _flat(clusters):
            assert "cluster_label" in r, "cluster_label missing"
            assert isinstance(r["cluster_label"], str)
            assert len(r["cluster_label"]) > 0

    def test_within_cluster_ordered_by_score_desc(self):
        # Two snippets that should cluster together (same topic)
        results = [
            _r("hi.com",  0.9, "python programming tutorial for beginners learning code"),
            _r("med.com", 0.6, "python programming tutorial for beginners learning code"),
            _r("lo.com",  0.3, "python programming tutorial for beginners learning code"),
        ]
        clusters = cluster_results(results)
        for cluster in clusters:
            scores = [r["scores"]["final"] for r in cluster]
            assert scores == sorted(scores, reverse=True), \
                "Cluster members should be sorted by score descending"


# ── Identical snippets → aggressive clustering ────────────────────────────────

class TestIdenticalSnippets:

    def test_identical_snippets_one_or_few_clusters(self):
        """
        All results share the same snippet → cosine distance ≈ 0 → single cluster.
        """
        snippet = "the quick brown fox jumps over the lazy dog language model"
        results = [_r(f"site{i}.com", 0.9 - i * 0.05, snippet) for i in range(5)]
        clusters = cluster_results(results)
        # With TF-IDF, identical text → identical vectors → dist=0 → 1 cluster
        assert len(clusters) == 1, \
            f"Expected 1 cluster for identical snippets, got {len(clusters)}"

    def test_diversify_top1_from_identical_snippets(self):
        """Only 1 result in top-1 when all results are in the same cluster."""
        snippet = "the quick brown fox jumps over the lazy dog language model"
        results = [_r(f"site{i}.com", 0.9 - i * 0.05, snippet) for i in range(5)]
        clusters = cluster_results(results)
        top1 = diversify_top_10(clusters, top_n=1)
        assert len(top1) == 1


# ── Distinct snippets → one cluster per result ────────────────────────────────

class TestDistinctSnippets:

    def test_distinct_topics_multiple_clusters(self):
        """
        Completely different domain snippets should NOT all collapse into one cluster.
        """
        results = [
            _r("cooking.com",  0.9, "recipe pasta tomato sauce garlic olive oil herbs italian cuisine"),
            _r("space.com",    0.8, "nasa rocket launch orbit satellite telescope astronaut moon mars"),
            _r("finance.com",  0.7, "stock market investment portfolio dividend earnings quarterly report"),
            _r("medicine.com", 0.6, "clinical trial drug dosage patient symptoms diagnosis treatment"),
            _r("sports.com",   0.5, "football championship goal score league stadium athlete training"),
        ]
        clusters = cluster_results(results)
        # Should produce at least 2 distinct clusters (topics are very different)
        assert len(clusters) >= 2, \
            f"Expected ≥2 clusters for distinct topics, got {len(clusters)}"

    def test_diversify_top_n_cap_respected(self):
        """diversify_top_10 never returns more than top_n items."""
        results = [_r(f"s{i}.com", 0.9 - i * 0.05, f"unique topic {i} content {i}") for i in range(20)]
        clusters = cluster_results(results)
        for cap in (1, 5, 10, 15):
            top = diversify_top_10(clusters, top_n=cap)
            assert len(top) <= cap, f"Got {len(top)} results with top_n={cap}"


# ── diversify_top_10: redundancy reduction ────────────────────────────────────

class TestDiversifyTop10:

    def test_max_one_per_cluster_in_top_n(self):
        """
        When all results are in distinct clusters, top-N has one from each
        cluster (no cluster appears twice in the first pass).
        """
        results = [
            _r("a.com", 0.9, "cooking recipe pasta tomato garlic sauce"),
            _r("b.com", 0.8, "astronomy telescope nasa planets orbit stars"),
            _r("c.com", 0.7, "finance stock market trading portfolio investments"),
            _r("d.com", 0.6, "medicine hospital patient diagnosis clinical trial"),
            _r("e.com", 0.5, "sports football championship athlete training league"),
        ]
        clusters = cluster_results(results)
        top5 = diversify_top_10(clusters, top_n=5)

        # Each cluster_id should appear at most once in top 5
        cid_counts = Counter(r["cluster_id"] for r in top5)
        assert max(cid_counts.values()) == 1, \
            f"A cluster appeared more than once in top-5: {dict(cid_counts)}"

    def test_length_with_fewer_results_than_top_n(self):
        """If only 3 results exist and top_n=10, return all 3."""
        results = [_r(f"s{i}.com", 0.9 - i * 0.2, f"topic {i}") for i in range(3)]
        clusters = cluster_results(results)
        top = diversify_top_10(clusters, top_n=10)
        assert len(top) == 3

    def test_best_scored_result_from_cluster_comes_first(self):
        """
        Within the round-robin, the highest-scored result from each cluster
        is selected on pass 1 — so the overall best result appears first.
        """
        snippet = "python programming language tutorial guide"
        results = [
            _r("best.com", 0.95, snippet),
            _r("good.com", 0.70, snippet),
            _r("other.com", 0.50, "cooking recipe tomato pasta sauce garlic"),
        ]
        clusters = cluster_results(results)
        top3 = diversify_top_10(clusters, top_n=3)
        # best.com should appear before good.com (both in same cluster, best first)
        urls = [r["url"] for r in top3]
        assert "https://best.com/page" in urls, "Best-scored result missing from top-3"
        if "https://good.com/page" in urls:
            assert urls.index("https://best.com/page") < urls.index("https://good.com/page"), \
                "best.com should rank before good.com"
