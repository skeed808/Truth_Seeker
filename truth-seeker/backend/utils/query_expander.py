"""
Query Expander — heuristics-only, zero external API calls.

Generates 3–5 semantically distinct query variants from a single user query.
The variants target different information spaces so that, when run in parallel,
they widen the result pool beyond what the original query alone would return.

Strategy (in order of priority):
  1. Original query                    — always included
  2. Academic/research framing         — "X research" / "X study"
  3. Technical-depth framing           — "how X works" / "X mechanism"
  4. Community/forum framing           — "X discussion" / "X forum"
  5. Synonym substitution              — replace one word with a domain synonym

Design principles:
  - Keep variants short (2–5 words) — better DDG performance
  - Avoid duplication (case-insensitive dedup)
  - Never produce a variant longer than original + 2 words
  - Return at most MAX_VARIANTS variants
"""
import re
from typing import List

MAX_VARIANTS = 5

# ── Synonym dictionary ────────────────────────────────────────────────────────
# key: word that might appear in query (lowercase)
# value: one or two alternative words to substitute

_SYNONYMS: dict[str, list[str]] = {
    # General terms
    "guide":          ["tutorial", "reference"],
    "tutorial":       ["guide", "howto"],
    "introduction":   ["overview", "primer"],
    "overview":       ["introduction", "summary"],
    "review":         ["analysis", "evaluation"],
    "comparison":     ["versus", "analysis"],
    "history":        ["origin", "evolution"],
    "explanation":    ["analysis", "breakdown"],
    "example":        ["demonstration", "case study"],
    "best":           ["top", "recommended"],
    # Technical
    "algorithm":      ["method", "technique"],
    "implementation": ["design", "architecture"],
    "performance":    ["efficiency", "optimization"],
    "security":       ["safety", "protection"],
    "programming":    ["development", "coding"],
    "language":       ["syntax", "specification"],
    "framework":      ["library", "toolkit"],
    "database":       ["storage", "persistence"],
    "network":        ["distributed system", "infrastructure"],
    "machine learning": ["deep learning", "neural networks"],
    "artificial intelligence": ["machine learning", "AI"],
    # Science / academia
    "science":        ["research", "field"],
    "study":          ["research", "analysis"],
    "theory":         ["principles", "foundations"],
    "process":        ["mechanism", "pathway"],
    "effect":         ["impact", "influence"],
    "analysis":       ["study", "investigation"],
    "biology":        ["biochemistry", "molecular biology"],
    "chemistry":      ["biochemistry", "chemical processes"],
    "physics":        ["mechanics", "dynamics"],
    "psychology":     ["neuroscience", "cognitive science"],
    # Society / culture
    "philosophy":     ["principles", "theory"],
    "politics":       ["policy", "governance"],
    "economics":      ["finance", "monetary policy"],
    "society":        ["culture", "social dynamics"],
    "design":         ["architecture", "principles"],
}

# ── Framing templates applied based on query characteristics ─────────────────
# (condition_words, template)
# template uses {q} as placeholder for the original query

_ACADEMIC_TRIGGERS = {
    "research", "study", "paper", "journal", "arxiv", "academic",
    "published", "proceedings", "thesis", "dissertation",
}
_TECHNICAL_TRIGGERS = {
    "how", "what", "why", "works", "mechanism", "explained",
    "algorithm", "process", "system", "architecture",
}
_FORUM_TRIGGERS = {
    "forum", "discuss", "community", "reddit", "opinion",
    "recommend", "advice", "experience",
}


def expand_query(query: str, max_variants: int = MAX_VARIANTS) -> List[str]:
    """
    Generate up to `max_variants` distinct query variants.
    Always starts with the original query.
    """
    q = query.strip()
    if not q:
        return [q]

    q_lower = q.lower()
    words   = q_lower.split()
    variants: list[str] = [q]

    # ── 1. Academic / research framing ───────────────────────────────────────
    if not any(t in q_lower for t in _ACADEMIC_TRIGGERS):
        if len(q.split()) <= 4:          # don't make long queries even longer
            variants.append(f"{q} research")

    # ── 2. Technical / explanatory framing ───────────────────────────────────
    if not any(t in q_lower for t in _TECHNICAL_TRIGGERS):
        # Convert "X Y Z" → "how X Y Z works" if short enough
        if len(words) <= 3:
            variants.append(f"how {q} works")
        else:
            variants.append(f"{q} explained")

    # ── 3. Community / forum framing ─────────────────────────────────────────
    if not any(t in q_lower for t in _FORUM_TRIGGERS):
        variants.append(f"{q} discussion")

    # ── 4. Synonym substitution ───────────────────────────────────────────────
    for original, substitutes in _SYNONYMS.items():
        # Match whole-word
        pattern = rf"\b{re.escape(original)}\b"
        if re.search(pattern, q_lower):
            for sub in substitutes:
                # Case-preserving replace
                candidate = re.sub(pattern, sub, q, flags=re.IGNORECASE).strip()
                if candidate.lower() != q.lower():
                    variants.append(candidate)
                    break  # one synonym sub per query
            break          # only substitute the first matching word

    # ── Dedup + truncate ──────────────────────────────────────────────────────
    seen: list[str] = []
    for v in variants:
        normalised = v.strip().lower()
        if normalised not in [s.strip().lower() for s in seen]:
            seen.append(v.strip())

    return seen[:max_variants]


def variants_for_scraping(query: str) -> List[str]:
    """
    Return ADDITIONAL query strings to feed into scrapers (excludes original).
    Capped at 3 to avoid hammering DDG with too many requests.
    """
    all_variants = expand_query(query, max_variants=5)
    # First item is always the original — skip it
    return all_variants[1:4]
