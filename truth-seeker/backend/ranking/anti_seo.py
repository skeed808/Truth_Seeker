"""
De-SEO / AI Content Detector

Heuristics-only approach — no external APIs, no ML models.
Detects patterns common to AI-generated and SEO-templated content:

  Signal 1 — Sentence length uniformity
    AI text tends to produce eerily similar sentence lengths.
    Measure coefficient of variation; low CV = suspicious.

  Signal 2 — Paragraph self-similarity
    Consecutive paragraphs that repeat vocabulary suggest filler/padding.
    Word-level Jaccard overlap between adjacent paragraph pairs.

  Signal 3 — Generic SEO/AI opener phrases
    A curated list of phrases that human writers rarely use but AI/SEO
    templates use constantly ("In this article", "First and foremost", etc.)

  Signal 4 — Heading density
    Template content is heavily sectioned into tiny heading+paragraph blocks.
    Very high heading:word ratio = likely template.

  Signal 5 — Stopword flood
    AI content is rich in connective tissue ("Furthermore", "Moreover",
    "In addition", "It is worth noting") vs. actual lexical content.

Returns ai_spam_score [0.0, 1.0].
  0.0 = almost certainly human-written, substantive content
  1.0 = very likely AI-generated / heavily templated SEO content
"""
import re
import math
from typing import Dict, List

from ranking.cleaner import count_pattern_matches

# ── Generic SEO / AI opener phrases ──────────────────────────────────────────
_GENERIC_PATTERNS = [
    r"\bin (this|the) (article|post|guide|tutorial|blog(\s+post)?|overview|write.?up)\b",
    r"\bin conclusion\b",
    r"\bto (sum|wrap) (it |things |up|)?up\b",
    r"\bfirst and foremost\b",
    r"\bit is (important|crucial|essential|worth|key|critical) to (note|mention|highlight|understand|know|consider|remember)\b",
    r"\bin today'?s? (world|digital age|landscape|era|society|fast.paced)\b",
    r"\bone of the (most|best|key|top|biggest|greatest|main|primary)\b",
    r"\bas (mentioned|discussed|noted|stated|highlighted|covered) (above|earlier|previously|below|in this)\b",
    r"\bwithout further ado\b",
    r"\bthat being said\b",
    r"\bnow that (we|you) (have|know|understand|are aware|'?ve)\b",
    r"\blet'?s (dive|delve|jump) (in|into|deeper|right in)\b",
    r"\bwhether you'?re? (a|an|new|experienced|looking|trying|a beginner|a professional)\b",
    r"\b(this|the) (comprehensive|ultimate|complete|detailed|definitive|in-depth) (guide|overview|tutorial|article|resource|post)\b",
    r"\bby the end of (this|the) (article|post|guide|tutorial)\b",
    r"\b(tips and tricks|pros and cons|dos and don'?ts)\b",
    r"\bstep[- ]by[- ]step\b",
    r"\bfrequently asked questions\b",
    r"\btable of contents\b",
    r"\bkey (takeaways?|points?|insights?|findings?)\b",
    r"\bare you (looking for|wondering|struggling|trying to|interested in)\b",
    r"\bin this (day and age|digital world|modern era)\b",
    r"\b(with that|having said that|all things considered|on the other hand)\b",
    r"\bit goes without saying\b",
    r"\b(read on|keep reading|scroll down) to (learn|find out|discover)\b",
]

# ── AI connective-tissue flood phrases ───────────────────────────────────────
_CONNECTIVE_FLOOD = [
    r"\bfurthermore\b", r"\bmoreover\b", r"\badditionally\b", r"\bin addition\b",
    r"\bconsequently\b", r"\btherefore\b", r"\bthus\b", r"\bhence\b",
    r"\bnevertheless\b", r"\bnonetheless\b", r"\bnotwithstanding\b",
    r"\bultimately\b", r"\bessentially\b", r"\bgenerally speaking\b",
    r"\boverall\b", r"\bin summary\b", r"\bto summarize\b",
    r"\bin other words\b", r"\bto put it simply\b", r"\bto put it another way\b",
]


def _sentence_uniformity_score(content: str) -> float:
    """
    Low coefficient of variation in sentence lengths → likely AI.
    Returns [0, 1] where 1.0 = very uniform.
    """
    sents = [s.strip() for s in re.split(r"[.!?]+", content) if len(s.strip()) > 8]
    if len(sents) < 6:
        return 0.0   # not enough data

    lens = [len(s.split()) for s in sents]
    mean = sum(lens) / len(lens)
    std = math.sqrt(sum((l - mean) ** 2 for l in lens) / len(lens))
    cv = std / max(mean, 1)

    # CV < 0.20 is suspicious; CV > 0.5 is natural
    if cv >= 0.50:
        return 0.0
    return float(max(0.0, (0.50 - cv) / 0.50))


def _paragraph_similarity_score(content: str) -> float:
    """
    Measures average word-overlap between consecutive paragraph pairs.
    High overlap → repetitive → AI filler.
    Returns [0, 1].
    """
    paras = [p.strip() for p in re.split(r"\n{2,}", content) if len(p.strip()) > 30]
    if len(paras) < 3:
        return 0.0

    overlaps = []
    for i in range(len(paras) - 1):
        wa = set(re.sub(r"[^a-z ]", "", paras[i].lower()).split()) - {"the","a","an","is","in","to","of","and","or"}
        wb = set(re.sub(r"[^a-z ]", "", paras[i+1].lower()).split()) - {"the","a","an","is","in","to","of","and","or"}
        if wa and wb:
            j = len(wa & wb) / len(wa | wb)
            overlaps.append(j)

    if not overlaps:
        return 0.0

    avg = sum(overlaps) / len(overlaps)
    # Jaccard > 0.15 between consecutive paragraphs is suspicious
    return float(min(avg / 0.15, 1.0))


def _generic_phrase_density(content: str) -> float:
    """
    Count generic SEO/AI phrases per 200 words. Returns [0, 1].
    """
    word_count = max(len(content.split()), 1)
    hits = count_pattern_matches(content, _GENERIC_PATTERNS)
    # 1 generic phrase per 200 words → 0.5; 2+ → max
    density = hits / (word_count / 200)
    return float(min(density / 2, 1.0))


def _heading_density_score(content: str) -> float:
    """
    Extremely high heading density relative to content = template.
    Returns [0, 1] where 1.0 = very template-heavy.
    """
    words = content.split()
    if not words:
        return 0.0

    # Match markdown headings and ALL-CAPS section titles
    heading_lines = re.findall(
        r"(?:^|\n)(#{1,4}\s+.+|[A-Z][A-Z &:]{4,40}(?:\n|$))",
        content,
        re.MULTILINE,
    )
    # More than 1 heading per 150 words is suspicious
    density = len(heading_lines) / (len(words) / 150)
    return float(min(density, 1.0))


def _connective_flood_score(content: str) -> float:
    """
    Overuse of linking adverbs is a strong AI writing tell.
    Returns [0, 1].
    """
    word_count = max(len(content.split()), 1)
    hits = count_pattern_matches(content, _CONNECTIVE_FLOOD)
    # 1 connective per 80 words is normal; 3+ per 80 words is suspicious
    density = hits / (word_count / 80)
    return float(min(density / 3, 1.0))


def _title_stuffing_score(result: Dict) -> float:
    """
    Detect SEO title factory patterns — keyword-stuffed, pipe-separated,
    formulaic titles that signal low-quality content farm output.

    Returns [0.0, 1.0] where 1.0 = heavy stuffing.

    Signals:
      - Pipe / dash / colon separations: "Best X | Top Y | Buy Z"
      - Formulaic number-list patterns: "10 Ways to…", "7 Tips for…"
      - Excessive length (> 80 chars often indicates keyword cramming)
      - All-caps word clusters (CLICKBAIT HEADLINE PATTERNS)
      - Round-bracket modifier spam: "Best X (Free & Cheap)"
    """
    title = (result.get("title") or "").strip()
    if not title or len(title) < 8:
        return 0.0

    score = 0.0

    # Pipe / em-dash / en-dash separations
    sep_count = len(re.findall(r"\s*[|–—]\s*", title))
    if sep_count >= 2:
        score += 0.55
    elif sep_count == 1:
        score += 0.20

    # "N Things/Ways/Tips/Steps…" formula
    if re.search(r"\b\d{1,2}\s+(things?|ways?|tips?|tricks?|steps?|methods?|ideas?|tools?|apps?|sites?)\b",
                 title, re.IGNORECASE):
        score += 0.25

    # Very long title
    if len(title) > 90:
        score += 0.30
    elif len(title) > 70:
        score += 0.12

    # Bracketed qualifiers at end: "Best X (2025) | Free & Cheap"
    bracket_hits = len(re.findall(r"[\(\[][^)\]]{3,30}[\)\]]", title))
    if bracket_hits >= 2:
        score += 0.20
    elif bracket_hits == 1:
        score += 0.05

    # All-caps words (more than 2 = clickbait)
    caps_words = re.findall(r"\b[A-Z]{3,}\b", title)
    if len(caps_words) >= 3:
        score += 0.20
    elif len(caps_words) == 2:
        score += 0.08

    # Common SEO formula opener
    if re.match(r"^\s*(best|top|ultimate|complete|definitive|full|free|cheap)\s+",
                title, re.IGNORECASE):
        score += 0.15

    return float(min(score, 1.0))


def score_ai_spam(result: Dict) -> float:
    """
    Aggregate AI/SEO spam score — v2 with title-stuffing signal.

    Returns [0.0, 1.0]:
      < 0.20 = probably genuine human writing
      0.20–0.50 = mixed / unclear
      > 0.50 = likely AI-generated or heavily templated / keyword-stuffed

    Sub-signal weights (sum to 1.0):
      sentence_uniformity   0.15 (was 0.20)
      paragraph_similarity  0.20 (was 0.25)
      generic_phrase_density 0.25 (was 0.30)
      heading_density       0.10
      connective_flood      0.15
      title_stuffing        0.15  ← new
    """
    content = result.get("content") or result.get("snippet", "")
    if not content or len(content.split()) < 60:
        # Content too short — still check title stuffing alone
        title_stuff = _title_stuffing_score(result)
        if title_stuff > 0:
            result.setdefault("anti_seo_detail", {"title_stuffing": round(title_stuff, 3)})
        return float(min(title_stuff * 0.5, 1.0))

    uniformity  = _sentence_uniformity_score(content)
    para_sim    = _paragraph_similarity_score(content)
    phrase_den  = _generic_phrase_density(content)
    heading_den = _heading_density_score(content)
    conn_flood  = _connective_flood_score(content)
    title_stuff = _title_stuffing_score(result)

    raw = (
        uniformity   * 0.15
        + para_sim   * 0.20
        + phrase_den * 0.25
        + heading_den* 0.10
        + conn_flood * 0.15
        + title_stuff* 0.15
    )

    score = float(max(0.0, min(1.0, raw)))

    result.setdefault("anti_seo_detail", {
        "sentence_uniformity":    round(uniformity, 3),
        "paragraph_similarity":   round(para_sim, 3),
        "generic_phrase_density": round(phrase_den, 3),
        "heading_density":        round(heading_den, 3),
        "connective_flood":       round(conn_flood, 3),
        "title_stuffing":         round(title_stuff, 3),
    })

    return score
