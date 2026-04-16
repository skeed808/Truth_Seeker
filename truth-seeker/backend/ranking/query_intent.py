"""
Query Intent Classifier — lightweight, <0.1 ms per call.

Classifies a query into one of four intent types that drive adaptive
crawl strategy and ranking weight adjustments:

  "navigational"        — user wants a specific site/service
  "freshness_sensitive" — user wants recent information
  "deep_research"       — multi-angle, investigative, controversial
  "informational"       — general knowledge / everything else

Decision priority (checked in order):
  1. freshness_sensitive  — temporal keywords are unambiguous
  2. navigational         — brand/login/site signals in short queries
  3. deep_research        — complex query patterns or length ≥ 5 words + signals
  4. informational        — catch-all default

No ML, no external dependencies.  All patterns compile once at module load.
"""
import re
from typing import List

# ── Pre-compiled signal patterns ─────────────────────────────────────────────

_FRESH_PATTERNS: List[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Explicit recency words
    r"\b(today|tonight|right now|this (morning|evening|hour|moment))\b",
    r"\b(yesterday|last (night|week|month))\b",
    r"\b(current(ly)?|latest|breaking|recent(ly)?|live|new(est)?|just (released?|announced?|launched?))\b",
    r"\b(this (week|month|year|season|quarter))\b",
    # Future/upcoming
    r"\b(upcoming|next (week|month|year)|soon|scheduled|forecast|predict)\b",
    # Time-anchored years (recent only)
    r"\b20(2[3-9]|3\d)\b",
    # News/event vocabulary
    r"\b(news|update|announcement|changelog|release notes?|patch notes?)\b",
    r"\b(who (won|is winning|leads?)|what (is happening|happened)|did .{1,30} happen)\b",
    # Sports / finance / weather
    r"\b(score|standings?|results?|rankings?|leaderboard)\b",
    r"\b(stock price|market (today|now)|earnings|quarterly)\b",
    r"\b(weather|temperature|forecast) (today|now|tonight|tomorrow)\b",
]]

_NAV_PATTERNS: List[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Login / account access
    r"\b(log ?in|sign ?in|sign ?up|log ?out|register|account|password reset)\b",
    # Download / install
    r"\b(download|install|get the (app|extension|plugin)|uninstall)\b",
    # Site pointers
    r"\b(official (site|website|page)|homepage|portal|dashboard|console)\b",
    # Known major brand names (navigational destinations)
    r"\b(youtube|reddit|twitter|x\.com|facebook|instagram|tiktok|"
    r"google|gmail|github|stackoverflow|wikipedia|amazon|netflix|"
    r"spotify|discord|slack|notion|figma|vercel|cloudflare)\b",
    # URL-like patterns in query
    r"\b\w+\.(com|org|net|io|dev|app|ai)\b",
]]

_DEEP_PATTERNS: List[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Causal / explanatory
    r"\b(why (does|do|is|are|was|were|did)|how (does|do|is|are) .{3,} work)\b",
    r"\b(explain|explanation of|what causes?|cause of|reason (for|why))\b",
    r"\b(history of|origin(s?) of|evolution of|development of)\b",
    # Comparative / analytical
    r"\b(compare|comparison|versus|vs\.?|difference(s?) between|pros and cons)\b",
    r"\b(analyse?|analysis|critique|criticism|review of|assessment)\b",
    r"\b(impact of|effect(s?) of|consequence(s?) of|implication(s?) of)\b",
    # Research / evidence
    r"\b(research|study|studies|evidence|data|statistics|paper|findings)\b",
    r"\b(theory|mechanism|underlying|fundamental(s?)|principle(s?))\b",
    # Controversial / multi-perspective
    r"\b(controversy|debate|disputed|problematic|issue(s?) with|problem with)\b",
    r"\b(argument(s?) (for|against)|case (for|against))\b",
    # Quoted phrases (precise lookup = research intent)
    r'"[^"]{4,}"',
    # Complex multi-part query (multiple clauses)
    r"\b(and also|as well as|in (addition|relation) to|along with)\b",
]]

# Minimum word count thresholds
_NAV_MAX_WORDS  = 4   # navigational queries are typically short
_DEEP_MIN_WORDS = 5   # long queries lean toward research


def classify_query_intent(query: str) -> str:
    """
    Classify *query* into one of four intent types.

    Returns:
        "freshness_sensitive" | "navigational" | "deep_research" | "informational"

    Performance: O(n × p) where n = query length and p = pattern count.
    Typically < 0.1 ms for queries up to 20 words.
    """
    if not query:
        return "informational"

    q       = query.strip()
    n_words = len(q.split())

    # ── 1. Freshness — check first; overrides everything else ────────────────
    # Even "facebook earnings today" is freshness-sensitive despite brand name.
    for pat in _FRESH_PATTERNS:
        if pat.search(q):
            return "freshness_sensitive"

    # ── 2. Navigational — only applies to short queries ──────────────────────
    # Long queries with a brand name are usually still research/informational.
    if n_words <= _NAV_MAX_WORDS:
        for pat in _NAV_PATTERNS:
            if pat.search(q):
                return "navigational"

    # ── 3. Deep research ──────────────────────────────────────────────────────
    deep_hits = sum(1 for pat in _DEEP_PATTERNS if pat.search(q))
    if deep_hits >= 1:
        return "deep_research"
    if n_words >= _DEEP_MIN_WORDS:
        # Long queries without explicit signals lean toward research
        return "deep_research"

    # ── 4. Default ────────────────────────────────────────────────────────────
    return "informational"
