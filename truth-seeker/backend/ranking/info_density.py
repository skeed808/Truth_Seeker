"""
Information Density v2

Significantly extends the original scorer with:
  + Sentence length variance   — natural human writing varies more than AI
  + Technical vocabulary boost — domain-specific terms signal information richness
  + Filler word penalty        — hedge words and padding dilute signal
  + N-gram repetition penalty  — repeated phrases = template / low-quality writing
  + Paragraph depth bonus      — multi-sentence paragraphs > one-liner bullets

All components return [0,1] and are weighted into a final score.
"""
import re
import math
from typing import Dict, List, Tuple

from ranking.cleaner import strip_boilerplate

# ── Filler / hedge words that dilute information density ─────────────────────
_FILLERS = frozenset({
    "basically", "simply", "very", "really", "just", "quite", "rather",
    "somewhat", "actually", "literally", "obviously", "clearly", "certainly",
    "needless", "course", "indeed", "perhaps", "maybe", "probably", "possibly",
    "extremely", "incredibly", "absolutely", "totally", "completely", "entirely",
    "truly", "surely", "undoubtedly", "admittedly", "frankly", "honestly",
})

# ── Stopwords to strip before diversity calculation ───────────────────────────
_STOPS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it",
    "for", "on", "with", "at", "by", "from", "this", "that", "was",
    "are", "be", "has", "had", "have", "not", "but", "as", "its",
    "into", "we", "they", "he", "she", "you", "i", "our", "their",
    "can", "will", "would", "could", "should", "may", "might",
    "been", "were", "being", "do", "did", "does", "so", "if", "then",
    "than", "also", "more", "some", "any", "all", "one", "two",
    "about", "after", "before", "when", "where", "which", "who", "what",
})


def _sentence_stats(content: str) -> Tuple[float, float, float]:
    """
    Returns (mean_len, std_dev, variance_score) for sentences in content.
    variance_score is 0→1 where higher = more natural variation.
    """
    sents = [s.strip() for s in re.split(r"[.!?]+", content) if len(s.strip()) > 8]
    if len(sents) < 4:
        return 15.0, 5.0, 0.5   # not enough data — neutral

    lens = [len(s.split()) for s in sents]
    mean = sum(lens) / len(lens)
    variance = sum((l - mean) ** 2 for l in lens) / len(lens)
    std = math.sqrt(variance)

    # Coefficient of variation: std / mean
    # Natural writing: CV ≈ 0.4–0.9  |  AI/templated: CV ≈ 0.1–0.25
    cv = std / max(mean, 1)

    # Score peaks at CV ≈ 0.6; very uniform (cv<0.2) or chaotic (cv>1.5) penalised
    if cv < 0.15:
        var_score = cv / 0.15 * 0.3      # very uniform → max 0.30
    elif cv <= 0.80:
        var_score = 0.30 + (cv - 0.15) / 0.65 * 0.70  # ramps up to 1.0
    else:
        var_score = max(0.0, 1.0 - (cv - 0.80) * 0.5)  # drops off for chaotic text

    return mean, std, float(max(0.0, min(1.0, var_score)))


def _technical_vocab_score(words: List[str]) -> float:
    """
    Estimate technical vocabulary density.
    Signals:
      - Uppercase acronyms (API, HTTP, JSON)
      - Words containing digits (IPv4, Python3, CO2)
      - Hyphenated compounds (state-of-the-art, well-known)
      - Very long words (>= 10 chars) that aren't obvious filler
    """
    if not words:
        return 0.0
    tech = 0
    for w in words:
        if w.isupper() and len(w) >= 2:
            tech += 2   # acronym = strong signal
        elif re.search(r"\d", w) and len(w) > 2:
            tech += 1   # alphanumeric compound
        elif "-" in w and len(w) > 6:
            tech += 1   # hyphenated compound
        elif len(w) >= 10:
            tech += 1   # long technical word
    ratio = tech / len(words)
    # Normalize: 5% technical density → 0.5, 10%+ → 1.0
    return float(min(ratio / 0.10, 1.0))


def _filler_penalty(words: List[str]) -> float:
    """Returns a penalty [0, 0.35] proportional to filler word density."""
    if not words:
        return 0.0
    count = sum(1 for w in words if w.lower() in _FILLERS)
    density = count / len(words)
    # 5% filler words → 0.175, 10%+ → 0.35 (max)
    return float(min(density / 0.10 * 0.35, 0.35))


def _ngram_repetition_penalty(words: List[str]) -> float:
    """
    Detects repetitive phrasing via trigram analysis.
    A high ratio of repeated trigrams indicates template writing.
    Returns a penalty [0, 0.30].
    """
    if len(words) < 40:
        return 0.0

    lower = [w.lower() for w in words if w.isalpha()]
    if len(lower) < 30:
        return 0.0

    trigrams = [tuple(lower[i:i+3]) for i in range(len(lower) - 2)]
    if not trigrams:
        return 0.0

    unique = len(set(trigrams))
    repeat_ratio = 1.0 - (unique / len(trigrams))
    # 20%+ repeated trigrams → max penalty
    return float(min(repeat_ratio / 0.20 * 0.30, 0.30))


def _paragraph_depth_score(content: str) -> float:
    """
    Multi-sentence paragraphs score higher than single-line bullets.
    Mean sentences-per-paragraph in range [2.5, 6] = ideal.
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", content) if len(p.strip()) > 20]
    if len(paragraphs) < 2:
        return 0.5   # can't assess

    spp = []  # sentences per paragraph
    for p in paragraphs:
        sents = [s for s in re.split(r"[.!?]+", p) if len(s.strip()) > 8]
        spp.append(len(sents))

    mean_spp = sum(spp) / len(spp)
    # Score peaks at ~4 sentences/paragraph
    score = 1.0 - abs(mean_spp - 4) / 5
    return float(max(0.0, min(1.0, score)))


def score_information_density(result: Dict) -> float:
    """
    Information Density v2 — full signal pipeline.

    Returns [0.0, 1.0].
    """
    raw_content = result.get("content") or result.get("snippet", "")
    if not raw_content:
        return 0.05

    content = strip_boilerplate(raw_content)
    words = content.split()
    word_count = len(words)

    if word_count < 30:
        return 0.05

    # ── A. Word count score (asymptotic) ─────────────────────────────────────
    count_score = 1.0 - math.exp(-word_count / 800)

    # ── B. Lexical diversity ──────────────────────────────────────────────────
    meaningful = [w.lower() for w in words if len(w) > 3 and w.lower() not in _STOPS]
    lex_diversity = len(set(meaningful)) / max(len(meaningful), 1)
    lex_diversity = min(lex_diversity, 0.95)

    # ── C. Sentence length variance ───────────────────────────────────────────
    _, _, var_score = _sentence_stats(content)

    # ── D. Technical vocabulary boost ────────────────────────────────────────
    tech_score = _technical_vocab_score(words)

    # ── E. Paragraph depth ───────────────────────────────────────────────────
    depth_score = _paragraph_depth_score(content)

    # ── Penalties ─────────────────────────────────────────────────────────────
    filler_pen  = _filler_penalty(words)
    ngram_pen   = _ngram_repetition_penalty(words)

    # ── Thin-content multiplier ───────────────────────────────────────────────
    thin_mult = min(word_count / 200, 1.0)

    # ── Weighted combination ──────────────────────────────────────────────────
    raw = (
        count_score   * 0.25
        + lex_diversity * 0.25
        + var_score     * 0.20
        + tech_score    * 0.15
        + depth_score   * 0.15
    ) - filler_pen - ngram_pen

    return float(max(0.0, min(1.0, raw * thin_mult)))
