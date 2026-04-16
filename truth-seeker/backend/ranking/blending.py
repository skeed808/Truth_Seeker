"""
Result Blending — diversity enforcement for the final result set.

Two constraints enforced over the top-N results:

  1. Domain diversity: max MAX_PER_DOMAIN results from any one domain in
     the top N.  Excess results are moved below rank N, preserving their
     relative order.

  2. Exploration injection: exploration-tagged results (is_exploration=True)
     are guaranteed at least MIN_EXPLORATION slots in the top N, provided
     that many exist.  This ensures echo-chamber-breaking candidates always
     surface even when their raw scores trail established high-trust domains.

Algorithm (O(n)):
  Pass 1 — build top-N with domain-diversity constraint.
  Pass 2 — count exploration results in top-N; inject missing ones by
            swapping out the lowest-ranked non-exploration results.
  Pass 3 — sort top-N by score (injection may disrupt order slightly).
  Remainder appended unchanged.

The full list length is preserved: len(output) == len(input).
"""
from typing import List, Dict


def blend_for_diversity(
    ranked: List[Dict],
    max_per_domain: int = 2,
    min_exploration: int = 2,
    top_n: int = 10,
) -> List[Dict]:
    """
    Apply diversity constraints to a ranked result list.

    Args:
        ranked:          Results sorted by score descending.
        max_per_domain:  Max results from any single domain within top_n.
        min_exploration: Minimum is_exploration=True results to inject into top_n.
        top_n:           Window over which constraints apply.

    Returns:
        Re-ordered list; len(output) == len(ranked).
    """
    if not ranked:
        return ranked

    # ── Pass 1: enforce domain diversity in top_n ─────────────────────────────
    top:      List[Dict] = []
    overflow: List[Dict] = []
    domain_count: Dict[str, int] = {}

    for result in ranked:
        domain = result.get("domain", "__unknown__")
        if len(top) < top_n and domain_count.get(domain, 0) < max_per_domain:
            top.append(result)
            domain_count[domain] = domain_count.get(domain, 0) + 1
        else:
            overflow.append(result)

    # ── Pass 2: inject exploration results if under-represented ───────────────
    expl_in_top = sum(1 for r in top if r.get("is_exploration"))
    deficit     = max(0, min_exploration - expl_in_top)

    if deficit > 0:
        # Gather exploration candidates from overflow (preserve their score order)
        available_expl = [r for r in overflow if r.get("is_exploration")]
        inject         = available_expl[:deficit]

        if inject:
            # Find non-exploration results in top to displace (lowest score first)
            displaceable = sorted(
                [i for i, r in enumerate(top) if not r.get("is_exploration")],
                key=lambda i: top[i].get("scores", {}).get("final", 0.0),
            )
            n_replace = min(len(inject), len(displaceable))

            for k in range(n_replace):
                displaced_idx = displaceable[k]
                displaced     = top[displaced_idx]
                injected      = inject[k]

                # Swap: inject goes into top, displaced moves to overflow
                top[displaced_idx] = injected
                overflow.remove(injected)
                overflow.append(displaced)

            # Re-sort top by score after injection (stable for equal scores)
            top.sort(
                key=lambda r: r.get("scores", {}).get("final", 0.0),
                reverse=True,
            )

    return top + overflow
