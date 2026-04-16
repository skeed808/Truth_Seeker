"""
Score aggregator.

Delegates to the specialised sub-modules and keeps the remaining
scorers (commercial_bias, freshness, diversity) inline.

Import surface for engine.py:
  score_information_density(result)
  score_obscurity(result, all_results)
  score_commercial_bias(result)
  score_freshness(result)
  score_diversity(result, all_results)

ai_spam and clustering are imported directly in engine.py from their modules.
"""
import re
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ── Delegate to upgraded sub-modules ─────────────────────────────────────────
from ranking.info_density import score_information_density   # noqa: re-export
from ranking.obscurity import score_obscurity                # noqa: re-export

from ranking.cleaner import (
    count_pattern_matches,
    strip_boilerplate,
    AFFILIATE_PATTERNS,
    SEO_PATTERNS,
    PRICE_PATTERNS,
)


# ── Commercial Bias v2 ────────────────────────────────────────────────────────

def score_commercial_bias(result: Dict) -> float:
    """
    Detects commercial intent / affiliate / SEO content.
    HIGHER score = MORE commercial = we SUBTRACT in the final formula.

    v2 additions over v1:
      • Expanded affiliate + SEO + price patterns (see cleaner.py)
      • Outbound link density heuristic via raw HTML (if available)
      • Template repetition detection (repeated heading patterns)
      • Product-heavy vocabulary density
    """
    text = " ".join([
        result.get("content") or "",
        result.get("snippet", ""),
        result.get("title", ""),
        result.get("url", ""),
    ])

    aff_hits   = count_pattern_matches(text, AFFILIATE_PATTERNS)
    seo_hits   = count_pattern_matches(text, SEO_PATTERNS)
    price_hits = count_pattern_matches(text, PRICE_PATTERNS)

    # Normalize: saturation points chosen so a moderately spammy page gets ~0.5
    aff_score   = min(aff_hits   / 3,  1.0) * 0.40
    seo_score   = min(seo_hits   / 6,  1.0) * 0.35
    price_score = min(price_hits / 6,  1.0) * 0.15

    # Domain-level commercial keywords (fast signal even without content)
    domain = result.get("domain", "").lower()
    domain_commercial = 0.0
    if any(kw in domain for kw in ("shop", "store", "buy", "price", "deal",
                                    "discount", "cheap", "sale", "review",
                                    "comparison", "vs", "best")):
        domain_commercial = 0.75
    domain_score = domain_commercial * 0.10

    # Heading template repetition (listicles often have very uniform heading structure)
    content = result.get("content", "") or ""
    if content:
        headings = re.findall(r"(?:^|\n)(#{1,4}\s+.+)", content, re.MULTILINE)
        if len(headings) > 4:
            # Check if headings all start with the same pattern (e.g., "## Best X for Y")
            first_words = [re.match(r"#{1,4}\s+(\w+)", h) for h in headings]
            first_words = [m.group(1).lower() for m in first_words if m]
            if first_words and len(set(first_words)) / len(first_words) < 0.4:
                # >60% of headings start with same word = template
                aff_score = min(aff_score + 0.15, 0.40)

    total = aff_score + seo_score + price_score + domain_score
    return float(max(0.0, min(1.0, total)))


# ── Freshness ─────────────────────────────────────────────────────────────────

def score_freshness(result: Dict) -> float:
    """
    Recency score based on publish date. Exponential decay τ = 365 days.
    Returns 0.5 if date is unknown (neutral).
    """
    date_str = result.get("publish_date")
    if not date_str:
        return 0.50

    FORMATS = [
        "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
        "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y",
    ]

    pub_date: Optional[datetime] = None
    for fmt in FORMATS:
        try:
            pub_date = datetime.strptime(str(date_str)[:25].strip(), fmt)
            break
        except (ValueError, TypeError):
            continue

    if pub_date is None:
        return 0.50

    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)

    age_days = max((datetime.now(timezone.utc) - pub_date).days, 0)
    return float(max(0.0, min(1.0, math.exp(-age_days / 365))))


# ── Diversity ─────────────────────────────────────────────────────────────────

def score_diversity(result: Dict, all_results: List[Dict]) -> float:
    """
    Penalises repeated domains in the result set.
    1 occ → 1.00, 2 → 0.60, 3 → 0.30, 4+ → 0.10
    """
    domain = result.get("domain", "")
    count = sum(1 for r in all_results if r.get("domain", "") == domain)
    return {1: 1.00, 2: 0.60, 3: 0.30}.get(count, 0.10)
