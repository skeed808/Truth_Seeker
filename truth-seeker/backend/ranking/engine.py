"""
Ranking Engine v4 — Anti-Bias, Intent-Adaptive, Anti-Gaming.

═══════════════════════════════════════════════════════════════════════════════
SCORING FORMULA (unchanged from v3)
═══════════════════════════════════════════════════════════════════════════════

  final = (info_density × w1) + (obscurity × w2)
        - (commercial_bias × w3) + (freshness × w4)
        + (diversity × w5) - (ai_spam × w6)
        + domain_trust_adj          ← ±0.04
        - over_optimization_penalty ← 0..0.12
        - duplicate_penalty         ← 0.10 if near-duplicate

Default weights: w1=0.26, w2=0.22, w3=0.20, w4=0.09, w5=0.09, w6=0.14

═══════════════════════════════════════════════════════════════════════════════
NEW IN v4
═══════════════════════════════════════════════════════════════════════════════

1. Intent-adaptive weight adjustments (via `intent` parameter):
     freshness_sensitive → freshness weight +0.15, obscurity -0.05
     deep_research       → obscurity +0.08, info_density +0.05
     navigational        → diversity +0.05, obscurity -0.08

2. Query over-optimisation penalty:
     If ≥ 80% of query terms appear in the title AND title ≥ 6 words,
     subtract up to 0.12 from final score.

3. Near-duplicate content penalty:
     Content fingerprint (MD5 of first 300 words) is compared across
     results.  The second occurrence of any fingerprint loses 0.10.

4. Anti-gaming via updated ai_spam (title-stuffing sub-signal).

5. blend_for_diversity() applied after scoring:
     - Max 2 results per domain in top 10
     - At least 2 exploration-tagged results injected into top 10
"""
import hashlib
import re
from typing import List, Dict, Optional, Set

from ranking.scores import (
    score_information_density,
    score_obscurity,
    score_commercial_bias,
    score_freshness,
    score_diversity,
)
from ranking.anti_seo import score_ai_spam
from ranking.clustering import classify_result
from ranking.blending import blend_for_diversity
from ranking import semantic_clustering
from ranking import link_graph as _link_graph
from ranking.feedback_boost import apply_feedback_boost

# ── Default weight vector ─────────────────────────────────────────────────────
_DEFAULT_WEIGHTS = {
    "info_density":    0.26,
    "obscurity":       0.22,
    "commercial_bias": 0.20,   # SUBTRACTED
    "freshness":       0.09,
    "diversity":       0.09,
    "ai_spam":         0.14,   # SUBTRACTED
}


def _compute_weights(prefs, intent: str = "informational") -> Dict[str, float]:
    """
    Translate user preferences + query intent into a weight vector.

    Adjustment priority (applied in order, then re-normalised):
      1. User sliders (underground_bias, freshness_bias)
      2. User toggles (deseo_mode, forums_priority)
      3. Query intent (freshness_sensitive / deep_research / navigational)
    """
    w = _DEFAULT_WEIGHTS.copy()

    # ── Slider: underground bias ──────────────────────────────────────────────
    ub = prefs.underground_bias - 0.5
    obs_delta = ub * 0.30
    w["obscurity"]    = max(0.05, w["obscurity"]    + obs_delta)
    w["info_density"] = max(0.05, w["info_density"] - obs_delta * 0.65)

    # ── Slider: freshness bias ────────────────────────────────────────────────
    fb = prefs.freshness_bias - 0.5
    w["freshness"] = max(0.0, min(0.40, w["freshness"] + fb * 0.15))

    # ── Toggle: De-SEO mode ───────────────────────────────────────────────────
    if getattr(prefs, "deseo_mode", False):
        w["ai_spam"]         = min(0.30, w["ai_spam"]         * 1.60)
        w["commercial_bias"] = min(0.35, w["commercial_bias"] * 1.40)

    # ── Toggle: Forums priority ───────────────────────────────────────────────
    if getattr(prefs, "forums_priority", False):
        w["obscurity"] = min(0.55, w["obscurity"] + 0.10)

    # ── Intent-adaptive adjustments ───────────────────────────────────────────
    if intent == "freshness_sensitive":
        # Weight recency heavily; discovery less important
        w["freshness"]  = min(0.40, w["freshness"]  + 0.15)
        w["obscurity"]  = max(0.05, w["obscurity"]  - 0.05)
    elif intent == "deep_research":
        # Prefer diverse, information-rich, independent sources
        w["obscurity"]    = min(0.45, w["obscurity"]    + 0.08)
        w["info_density"] = min(0.38, w["info_density"] + 0.05)
    elif intent == "navigational":
        # User wants the thing itself, not obscure alternatives
        w["diversity"]  = min(0.20, w["diversity"]  + 0.05)
        w["obscurity"]  = max(0.05, w["obscurity"]  - 0.08)

    # ── Re-normalise positive weights ─────────────────────────────────────────
    neg_sum  = w["commercial_bias"] + w["ai_spam"]
    pos_keys = ["info_density", "obscurity", "freshness", "diversity"]
    pos_total = sum(w[k] for k in pos_keys)
    target_pos = max(0.40, 1.0 - neg_sum)
    if pos_total > 0:
        scale = target_pos / pos_total
        for k in pos_keys:
            w[k] = round(w[k] * scale, 4)

    return {k: round(v, 4) for k, v in w.items()}


# ── Anti-gaming helpers ───────────────────────────────────────────────────────

def _content_fingerprint(result: Dict) -> str:
    """
    MD5 of the first 300 lower-cased words of content.
    Used to detect near-duplicate pages (scrapers often return the same content).
    Returns empty string if content is too short to fingerprint meaningfully.
    """
    text = (result.get("content") or result.get("snippet", "")).lower()
    words = text.split()
    if len(words) < 60:
        return ""
    return hashlib.md5(" ".join(words[:300]).encode()).hexdigest()[:16]


def _over_optimization_penalty(title: str, query: str) -> float:
    """
    Detect keyword stuffing aimed at a specific query.

    If ≥ 80% of meaningful query terms appear verbatim in the title AND
    the title is sufficiently long to be considered a stuffed title
    (≥ 6 words), apply a scoring penalty.

    Returns a penalty in [0.0, 0.12].  Zero for short/empty queries.
    """
    if not query or not title:
        return 0.0

    query_words = [w for w in re.sub(r"[^\w\s]", " ", query).lower().split()
                   if len(w) > 3]
    if len(query_words) < 2:
        return 0.0

    title_lower = title.lower()
    hits  = sum(1 for w in query_words if w in title_lower)
    ratio = hits / len(query_words)
    title_word_count = len(title.split())

    if ratio >= 0.80 and title_word_count >= 6:
        return 0.12
    if ratio >= 0.65 and title_word_count >= 8:
        return 0.07
    return 0.0


# ── Main ranking function ─────────────────────────────────────────────────────

def rank_results(
    results:      List[Dict],
    prefs,
    domain_trust: Optional[Dict[str, float]] = None,
    query:        str = "",
    intent:       str = "informational",
    feedback_map: Optional[Dict[str, int]] = None,
) -> List[Dict]:
    """
    v4 ranking pipeline.

    Args:
        results:      Result dicts (need title, url, domain, content/snippet).
        prefs:        SearchPreferences (user sliders + toggles).
        domain_trust: {domain: decayed_trust_score} from cache memory.
        query:        Original search query (for over-optimisation check).
        intent:       Classified query intent (affects weight vector).

    Returns:
        Sorted, transparency-annotated result list with diversity blending applied.
    """
    weights = _compute_weights(prefs, intent=intent)
    scored:       List[Dict]   = []
    seen_fingerprints: Set[str] = set()

    for result in results:
        # ── 6-signal scoring ──────────────────────────────────────────────────
        info       = score_information_density(result)
        obscurity  = score_obscurity(result, results)
        commercial = score_commercial_bias(result)
        freshness  = score_freshness(result)
        diversity  = score_diversity(result, results)
        ai_spam    = score_ai_spam(result)   # sets result["anti_seo_detail"]
        cluster    = classify_result(result)

        final = (
            info       * weights["info_density"]
            + obscurity  * weights["obscurity"]
            - commercial * weights["commercial_bias"]
            + freshness  * weights["freshness"]
            + diversity  * weights["diversity"]
            - ai_spam    * weights["ai_spam"]
        )

        # ── Domain trust adjustment (±0.04 max) ───────────────────────────────
        if domain_trust:
            t = domain_trust.get(result.get("domain", ""))
            if t is not None:
                final += (t - 0.5) * 0.08   # avg_score=0.5 → 0 adj

        # ── Over-optimisation penalty (query-aware) ────────────────────────────
        over_opt = _over_optimization_penalty(result.get("title", ""), query)
        final   -= over_opt

        # ── Near-duplicate penalty ─────────────────────────────────────────────
        fp = _content_fingerprint(result)
        if fp and fp in seen_fingerprints:
            final -= 0.10   # penalise, don't remove entirely
        elif fp:
            seen_fingerprints.add(fp)

        final = max(0.0, min(1.0, final))

        # ── Transparency payload ──────────────────────────────────────────────
        result["cluster"]       = cluster
        result["scores"]        = {
            "final":           round(final, 3),
            "info_density":    round(info, 3),
            "obscurity":       round(obscurity, 3),
            "commercial_bias": round(commercial, 3),
            "freshness":       round(freshness, 3),
            "diversity":       round(diversity, 3),
            "ai_spam":         round(ai_spam, 3),
        }
        result["weights_used"]  = weights
        result.pop("content", None)   # strip bulk before serialisation

        scored.append(result)

    scored.sort(key=lambda r: r["scores"]["final"], reverse=True)

    # ── Link graph authority boost (top 50 only) ──────────────────────────────
    # Domains linked to by other high-scoring results get a small trust boost.
    # Applied after the initial sort so link relationships are stable; scored is
    # re-sorted afterwards before semantic clustering.
    head50 = scored[:50]
    for result in head50:
        d       = result.get("domain", "")
        inbound = _link_graph.count_inbound_links(d, head50)
        boost   = _link_graph.boost_by_inbound_authority(d, inbound, domain_trust or {})
        result["trust_boost"] = boost
        if boost > 0:
            result["scores"]["final"] = round(
                min(1.0, result["scores"]["final"] + boost), 3
            )
    if any(r.get("trust_boost", 0) > 0 for r in head50):
        head50.sort(key=lambda r: r["scores"]["final"], reverse=True)
        scored = head50 + scored[50:]

    # ── Semantic clustering + diversification (top 50 only) ──────────────────
    # Cluster by snippet/title similarity; rebuild the top-10 so that each
    # cluster contributes at most one result per round-robin pass.
    # Results beyond position 50 are appended unchanged (overflow).
    if len(scored) > 5:
        head, tail = scored[:50], scored[50:]
        clusters          = semantic_clustering.cluster_results(head)
        diverse_head      = semantic_clustering.diversify_top_10(clusters, top_n=10)
        # Collect the non-diverse remainder (positions 11-50) in score order
        diverse_urls      = {r["url"] for r in diverse_head}
        remainder         = [r for r in head if r["url"] not in diverse_urls]
        scored            = diverse_head + remainder + tail

    # ── Diversity blending: domain cap + exploration injection ────────────────
    if len(scored) > 5:
        scored = blend_for_diversity(
            scored,
            max_per_domain=2,
            min_exploration=2,
            top_n=10,
        )

    # ── Feedback boost (Phase 7) ──────────────────────────────────────────────
    if feedback_map:
        scored = apply_feedback_boost(scored, feedback_map)

    return scored
