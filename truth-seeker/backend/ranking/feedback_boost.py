"""
Feedback Boost — Phase 7

Applies a small score adjustment to results that have received user votes.
Called after semantic clustering so cluster assignments are preserved.

Public API
----------
apply_feedback_boost(results, feedback_map) -> list[dict]
"""
from __future__ import annotations

from typing import Dict, List

_BOOST_UP   =  0.03
_BOOST_DOWN = -0.03


def apply_feedback_boost(
    results:      List[Dict],
    feedback_map: Dict[str, int],
) -> List[Dict]:
    """
    Adjust `scores.final` for results present in feedback_map, then re-sort.

    feedback_map: {url: aggregate_vote_sum}  (from user_feedback.get_feedback_map)
    A positive aggregate → +0.03 boost.
    A negative aggregate → -0.03 penalty.
    Zero or absent        → no change.

    Cluster and exploration tags are preserved unchanged.
    """
    if not feedback_map:
        return results

    adjusted = False
    for result in results:
        url  = result.get("url", "")
        vote = feedback_map.get(url, 0)
        if vote == 0:
            continue

        delta = _BOOST_UP if vote > 0 else _BOOST_DOWN
        scores = result.setdefault("scores", {})
        scores["final"] = round(
            max(0.0, min(1.0, scores.get("final", 0.0) + delta)), 3
        )
        result["feedback_vote"] = vote
        adjusted = True

    if adjusted:
        results = sorted(results, key=lambda r: r["scores"].get("final", 0.0), reverse=True)

    return results
