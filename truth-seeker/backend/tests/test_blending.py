"""
Unit tests for blend_for_diversity.

Three invariants under test:
  1. Domain cap: no more than max_per_domain results from any domain in top_n.
  2. Exploration injection: at least min_exploration is_exploration=True results
     in top_n, provided that many exist across the whole list.
  3. Length preservation: len(output) == len(input) always.

Edge-case focus: fewer exploration results available than min_exploration demands.
"""
from collections import Counter
from ranking.blending import blend_for_diversity


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _r(domain: str, score: float, exploration: bool = False) -> dict:
    """Minimal result dict sufficient for blend_for_diversity."""
    return {
        "domain":       domain,
        "scores":       {"final": score},
        "is_exploration": exploration,
    }


def _top(result_list, n=10):
    return result_list[:n]


def _expl_count(result_list, n=10):
    return sum(1 for r in result_list[:n] if r.get("is_exploration"))


# ── Length preservation (always tested first — fundamental invariant) ─────────

class TestLengthPreservation:

    def test_empty_list(self):
        assert blend_for_diversity([]) == []

    def test_single_result(self):
        assert len(blend_for_diversity([_r("a.com", 0.9)])) == 1

    def test_exactly_top_n(self):
        ranked = [_r(f"s{i}.com", 0.9 - i * 0.05) for i in range(10)]
        assert len(blend_for_diversity(ranked)) == 10

    def test_more_than_top_n(self):
        ranked = [_r(f"s{i}.com", 0.9 - i * 0.04) for i in range(20)]
        assert len(blend_for_diversity(ranked)) == 20

    def test_fewer_than_top_n(self):
        ranked = [_r(f"s{i}.com", 0.9 - i * 0.1) for i in range(5)]
        assert len(blend_for_diversity(ranked)) == 5


# ── Domain cap ────────────────────────────────────────────────────────────────
#
# The cap is applied only to the `top` portion (up to top_n items).  To observe
# it in `result[:top_n]`, the input must supply enough *distinct* domains to
# fill all top_n slots — otherwise overflow results (no cap) spill in.

class TestDomainCap:

    def test_cap_1_per_domain_enforced(self):
        # 5 high-scoring same.com + 10 unique domains to fill all top_n slots
        ranked = (
            [_r("same.com", 0.9 - i * 0.01) for i in range(5)]
            + [_r(f"unique{i}.com", 0.50 - i * 0.02) for i in range(10)]
        )
        ranked.sort(key=lambda r: r["scores"]["final"], reverse=True)
        result = blend_for_diversity(ranked, max_per_domain=1, min_exploration=0, top_n=10)
        counts = Counter(r["domain"] for r in result[:10])
        assert counts.get("same.com", 0) <= 1
        assert all(v <= 1 for v in counts.values())
        assert len(result) == 15

    def test_cap_2_per_domain_enforced(self):
        # 5 high-scoring same.com + 10 unique domains
        ranked = (
            [_r("same.com", 0.9 - i * 0.01) for i in range(5)]
            + [_r(f"unique{i}.com", 0.50 - i * 0.02) for i in range(10)]
        )
        ranked.sort(key=lambda r: r["scores"]["final"], reverse=True)
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=0, top_n=10)
        counts = Counter(r["domain"] for r in result[:10])
        assert counts.get("same.com", 0) <= 2
        assert all(v <= 2 for v in counts.values())

    def test_excess_results_pushed_to_overflow(self):
        # 5 from same.com (high scores) + 9 unique (lower) — top_n=10 fills completely
        # with cap=1: only 1 same.com in top, 4 displaced to overflow
        ranked = (
            [_r("same.com", 0.9 - i * 0.01) for i in range(5)]
            + [_r(f"other{i}.com", 0.80 - i * 0.02) for i in range(9)]
        )
        ranked.sort(key=lambda r: r["scores"]["final"], reverse=True)
        result = blend_for_diversity(ranked, max_per_domain=1, min_exploration=0, top_n=10)
        same_in_top = sum(1 for r in result[:10] if r["domain"] == "same.com")
        assert same_in_top == 1
        assert len(result) == 14


# ── Exploration injection — the primary edge-case focus ───────────────────────

class TestExplorationInjection:

    def test_zero_exploration_available_no_injection(self):
        """
        When no results anywhere have is_exploration=True, top is unchanged
        and the function doesn't crash.
        """
        ranked = [_r(f"site{i}.com", 0.9 - i * 0.05) for i in range(12)]
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)
        assert _expl_count(result, 10) == 0
        assert len(result) == 12

    def test_one_exploration_in_overflow_only_one_injected(self):
        """
        Deficit = 2 but only 1 exploration result exists in overflow.
        Only 1 should be injected — no crash, no phantom injection.
        """
        ranked = (
            [_r(f"site{i}.com", 0.9 - i * 0.05) for i in range(10)]   # fill top
            + [_r("explore.net", 0.10, exploration=True)]               # 1 in overflow
        )
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)
        assert _expl_count(result, 10) == 1
        assert len(result) == 11

    def test_two_exploration_in_overflow_full_deficit_filled(self):
        """Deficit = 2 with 2 exploration results → both are injected."""
        ranked = (
            [_r(f"site{i}.com", 0.9 - i * 0.05) for i in range(10)]
            + [_r("expl1.net", 0.12, exploration=True)]
            + [_r("expl2.net", 0.10, exploration=True)]
        )
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)
        assert _expl_count(result, 10) == 2
        assert len(result) == 12

    def test_three_exploration_in_overflow_only_deficit_injected(self):
        """Only min_exploration (2) are injected, not all available exploration results."""
        ranked = (
            [_r(f"site{i}.com", 0.9 - i * 0.05) for i in range(10)]
            + [_r(f"expl{i}.net", 0.10 - i * 0.01, exploration=True) for i in range(3)]
        )
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)
        # Exactly 2 injected (deficit=2), not 3
        assert _expl_count(result, 10) == 2
        assert len(result) == 13

    def test_one_exploration_already_in_top_only_one_more_injected(self):
        """
        1 exploration result already earned its way into top via score →
        deficit = 1 → only 1 more injected from overflow.
        """
        ranked = (
            [_r("expl-hi.net", 0.88, exploration=True)]          # already in top
            + [_r(f"site{i}.com", 0.80 - i * 0.05) for i in range(9)]
            + [_r("expl-lo.net", 0.05, exploration=True)]         # overflow candidate
        )
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)
        assert _expl_count(result, 10) == 2
        assert len(result) == 11

    def test_two_exploration_already_in_top_no_injection_needed(self):
        """min_exploration already satisfied → no overflow injection."""
        ranked = (
            [_r("expl1.net", 0.90, exploration=True)]
            + [_r("expl2.net", 0.85, exploration=True)]
            + [_r(f"site{i}.com", 0.70 - i * 0.05) for i in range(8)]
            + [_r("expl3.net", 0.05, exploration=True)]   # overflow — should NOT be injected
        )
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)
        # No extra injection because deficit = 0
        assert _expl_count(result, 10) == 2
        assert len(result) == 11

    def test_no_displaceable_non_exploration_when_top_is_all_exploration(self):
        """
        If all top-N slots are occupied by exploration results, there is
        nothing non-exploration to displace — the function must not crash.
        """
        # 10 high-scoring exploration fill top; 2 more exploration in overflow
        ranked = (
            [_r(f"expl{i}.net", 0.9 - i * 0.03, exploration=True) for i in range(10)]
            + [_r(f"expl_lo{i}.net", 0.1 - i * 0.01, exploration=True) for i in range(2)]
        )
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)
        # Already >= min_exploration in top — should complete without error
        assert _expl_count(result, 10) >= 2
        assert len(result) == 12

    def test_min_exploration_zero_no_injection(self):
        """min_exploration=0 disables injection entirely."""
        ranked = (
            [_r(f"site{i}.com", 0.9 - i * 0.05) for i in range(10)]
            + [_r("expl.net", 0.80, exploration=True)]
        )
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=0, top_n=10)
        # exploration result was scored high enough to enter top on its own
        # but min_exploration=0 means no forced injection
        assert len(result) == 11

    def test_displaced_results_appear_in_overflow_not_lost(self):
        """
        Results displaced from top during exploration injection must appear
        in the overflow section — no results are lost.
        """
        regular = [_r(f"site{i}.com", 0.9 - i * 0.05) for i in range(10)]
        explorations = [
            _r("expl1.net", 0.05, exploration=True),
            _r("expl2.net", 0.04, exploration=True),
        ]
        ranked = regular + explorations
        result = blend_for_diversity(ranked, max_per_domain=2, min_exploration=2, top_n=10)

        assert len(result) == 12
        all_urls = [r["domain"] for r in result]
        # Every input domain must appear exactly once in the output
        for r in ranked:
            assert all_urls.count(r["domain"]) == 1
