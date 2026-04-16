"""
Phase 6b + 6c tests.

6b — Link graph:
  - count_inbound_links: domain, self-link exclusion, missing _outbound_links
  - boost_by_inbound_authority: formula, cap, empty graph, unknown-source default

6c — Query memory:
  - log + retrieve, search_count increment, sort order
"""
import pytest
from ranking.link_graph import count_inbound_links, boost_by_inbound_authority
from cache.query_memory import log_successful_query, get_similar_queries


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _result(domain: str, links: list = None) -> dict:
    r = {"domain": domain}
    if links is not None:
        r["_outbound_links"] = links
    return r


# ── count_inbound_links ───────────────────────────────────────────────────────

class TestCountInboundLinks:

    def test_counts_links_correctly(self):
        """2 links from source1, 2 from source2, 0 from source3."""
        results = [
            _result("source1.com", ["https://target.com/page1", "https://other.com"]),
            _result("source2.com", ["https://target.com/a", "https://target.com/b"]),
            _result("source3.com", ["https://unrelated.net/x"]),
            _result("source4.com"),   # no _outbound_links key at all
        ]
        inbound = count_inbound_links("target.com", results)
        assert inbound.get("source1.com", 0) == 1
        assert inbound.get("source2.com", 0) == 2
        assert "source3.com" not in inbound
        assert "source4.com" not in inbound

    def test_skips_self_links(self):
        """The target domain must not count links from itself."""
        results = [
            _result("target.com", ["https://target.com/internal", "https://target.com/other"]),
        ]
        inbound = count_inbound_links("target.com", results)
        assert "target.com" not in inbound
        assert inbound == {}

    def test_no_outbound_links_graceful(self):
        """Results with None or missing _outbound_links are skipped cleanly."""
        results = [
            _result("a.com"),
            _result("b.com", None),
            _result("c.com", []),
        ]
        inbound = count_inbound_links("target.com", results)
        assert inbound == {}

    def test_multiple_results_same_source(self):
        """Two results from the same source domain accumulate counts."""
        results = [
            _result("source.com", ["https://target.com/1"]),
            _result("source.com", ["https://target.com/2", "https://target.com/3"]),
        ]
        inbound = count_inbound_links("target.com", results)
        assert inbound["source.com"] == 3

    def test_domain_substring_matching(self):
        """Link is matched if domain string appears anywhere in the URL."""
        results = [
            _result("linker.com",  ["https://completely-unrelated.org/page"]),  # no match
            _result("linker2.com", ["https://target.com/deep/path"]),            # direct match
            _result("linker3.com", ["https://cdn.target.com/asset.js"]),         # subdomain match
        ]
        inbound = count_inbound_links("target.com", results)
        assert "linker.com"  not in inbound
        assert inbound.get("linker2.com", 0) == 1
        assert inbound.get("linker3.com", 0) == 1

    def test_empty_results_returns_empty(self):
        assert count_inbound_links("target.com", []) == {}


# ── boost_by_inbound_authority ────────────────────────────────────────────────

class TestBoostByInboundAuthority:

    def test_correct_calculation(self):
        """boost = (trust_A + trust_B) * 0.02 = (0.8 + 0.7) * 0.02 = 0.03"""
        inbound   = {"highsource.com": 2, "medsource.com": 1}
        trust_map = {"highsource.com": 0.8, "medsource.com": 0.7}
        boost = boost_by_inbound_authority("target.com", inbound, trust_map)
        assert abs(boost - 0.03) < 0.001

    def test_boost_between_0_and_005(self):
        """Result is always within the documented range."""
        inbound   = {"a.com": 1, "b.com": 3}
        trust_map = {"a.com": 0.9, "b.com": 0.85}
        boost = boost_by_inbound_authority("target.com", inbound, trust_map)
        assert 0.0 <= boost <= 0.05

    def test_cap_at_005(self):
        """Many high-trust sources must not exceed the 0.05 cap."""
        inbound   = {f"s{i}.com": 1 for i in range(20)}
        trust_map = {f"s{i}.com": 0.95 for i in range(20)}
        boost = boost_by_inbound_authority("target.com", inbound, trust_map)
        assert boost == 0.05

    def test_empty_inbound_returns_zero(self):
        boost = boost_by_inbound_authority("target.com", {}, {"x.com": 0.9})
        assert boost == 0.0

    def test_unknown_source_uses_neutral_trust(self):
        """Sources absent from trust_map default to 0.5 (neutral)."""
        inbound   = {"unknown.com": 1}
        trust_map = {}   # no known scores
        boost = boost_by_inbound_authority("target.com", inbound, trust_map)
        # 0.5 * 0.02 = 0.01
        assert abs(boost - 0.01) < 0.001

    def test_link_count_not_used_in_formula(self):
        """
        A source with 10 links counts the same as one with 1 link —
        only the unique source domain trust is summed.
        """
        inbound_one  = {"a.com": 1}
        inbound_ten  = {"a.com": 10}
        trust_map    = {"a.com": 0.8}
        boost_one = boost_by_inbound_authority("target.com", inbound_one, trust_map)
        boost_ten = boost_by_inbound_authority("target.com", inbound_ten, trust_map)
        assert boost_one == boost_ten


# ── Query memory ──────────────────────────────────────────────────────────────

class TestQueryMemory:

    def test_log_and_retrieve(self, tmp_path):
        """Both logged queries appear in get_similar_queries results."""
        db = tmp_path / "qmem.db"
        log_successful_query("quantum computing", "deep_research", db_path=db)
        log_successful_query("quantum computing research", "deep_research", db_path=db)

        # "quantum" is a substring of both stored queries → both returned
        results = get_similar_queries("quantum", threshold=0.99, db_path=db)
        found = [r[0] for r in results]
        assert "quantum computing" in found
        assert "quantum computing research" in found

    def test_all_intents_correct(self, tmp_path):
        """Every returned row has the correct intent."""
        db = tmp_path / "qmem.db"
        log_successful_query("quantum computing", "deep_research", db_path=db)
        log_successful_query("quantum computing research", "deep_research", db_path=db)
        results = get_similar_queries("quantum", threshold=0.99, db_path=db)
        for _, intent, _ in results:
            assert intent == "deep_research"

    def test_search_count_increments(self, tmp_path):
        """Same query logged 3 times → search_count == 3."""
        db = tmp_path / "qmem.db"
        for _ in range(3):
            log_successful_query("machine learning", "informational", db_path=db)
        results = get_similar_queries("machine learning", threshold=0.9, db_path=db)
        assert len(results) == 1
        assert results[0][2] == 3

    def test_sorted_by_search_count_desc(self, tmp_path):
        """More-frequent query appears before less-frequent one."""
        db = tmp_path / "qmem.db"
        log_successful_query("python tutorial", "informational", db_path=db)
        for _ in range(5):
            log_successful_query("python programming", "informational", db_path=db)
        results = get_similar_queries("python", threshold=0.3, db_path=db)
        # "python programming" (count=5) must precede "python tutorial" (count=1)
        counts = [r[2] for r in results]
        assert counts == sorted(counts, reverse=True)

    def test_no_false_positives_at_high_threshold(self, tmp_path):
        """Unrelated query must not appear in results at high threshold."""
        db = tmp_path / "qmem.db"
        log_successful_query("javascript frameworks", "informational", db_path=db)
        log_successful_query("quantum computing", "deep_research", db_path=db)
        # "javascript" at threshold=0.85 should not match "quantum computing"
        results = get_similar_queries("javascript", threshold=0.85, db_path=db)
        queries = [r[0] for r in results]
        assert "quantum computing" not in queries
        assert "javascript frameworks" in queries

    def test_different_query_different_hash(self, tmp_path):
        """Two distinct queries are stored as separate rows."""
        db = tmp_path / "qmem.db"
        log_successful_query("rust language", "informational", db_path=db)
        log_successful_query("go language", "informational", db_path=db)
        # Both should be retrievable individually
        rust = get_similar_queries("rust language", threshold=0.9, db_path=db)
        go   = get_similar_queries("go language",   threshold=0.9, db_path=db)
        assert len(rust) >= 1
        assert len(go)   >= 1
        assert rust[0][0] != go[0][0]

    def test_empty_db_returns_empty_list(self, tmp_path):
        db = tmp_path / "empty.db"
        results = get_similar_queries("anything", db_path=db)
        assert results == []
