"""
Link Graph Authority — Phase 6b

Derives a small authority boost from inbound-link signals within the current
result batch.  No external crawl required: results already carry
`_outbound_links` when `include_links=True` was passed to extract_content_batch.

Public API
----------
count_inbound_links(domain, results)           → {source_domain: link_count}
boost_by_inbound_authority(domain, inbound, trust_map) → float (0.0–0.05)
"""
from __future__ import annotations

from typing import Dict, List


def count_inbound_links(domain: str, results: List[Dict]) -> Dict[str, int]:
    """
    Count how many outbound links from each result in `results` point to
    `domain`.

    Args:
        domain:  Target domain to check inbound links for.
        results: Result dicts that may contain `_outbound_links` (list of URLs).

    Returns:
        {source_domain: number_of_links_to_target}  — only non-zero entries.
        Results whose domain equals the target are skipped (no self-links).
        Results without `_outbound_links` are skipped gracefully.
    """
    inbound: Dict[str, int] = {}

    for r in results:
        source = r.get("domain", "")
        if not source or source == domain:
            continue

        links = r.get("_outbound_links") or []
        count = sum(1 for link in links if isinstance(link, str) and domain in link)
        if count > 0:
            inbound[source] = inbound.get(source, 0) + count

    return inbound


def boost_by_inbound_authority(
    domain: str,
    inbound_graph: Dict[str, int],
    trust_map: Dict[str, float],
) -> float:
    """
    Translate inbound link graph into a score boost using source domain trust.

    Formula:
        boost = sum(trust_score[source] for source in inbound_graph) * 0.02

    Each unique linking domain contributes its trust score once (link *count*
    is not used — prevents a single domain from gaming the signal by linking
    many times).  Unknown source domains default to neutral trust (0.5).

    Args:
        domain:        Target domain (unused in formula; kept for call-site clarity).
        inbound_graph: Output of count_inbound_links — {source_domain: count}.
        trust_map:     {domain: trust_score} from the page cache.

    Returns:
        Boost in [0.0, 0.05].
    """
    if not inbound_graph:
        return 0.0

    total_trust = sum(trust_map.get(src, 0.5) for src in inbound_graph)
    return min(0.05, round(total_trust * 0.02, 4))
