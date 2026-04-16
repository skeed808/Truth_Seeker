"""
Unit tests for classify_query_intent.

Focused on edge cases that are easy to get wrong:
  - Brand name in a long query (should NOT be navigational)
  - Year patterns (only 2023-2039 trigger freshness_sensitive)
  - Priority ordering (freshness beats nav beats deep beats info)
"""
import pytest
from ranking.query_intent import classify_query_intent


# ── helpers ───────────────────────────────────────────────────────────────────

def intent(q):
    return classify_query_intent(q)


# ── Freshness-sensitive ───────────────────────────────────────────────────────

class TestFreshnessSensitive:
    """Freshness signals must win over every other signal — highest priority."""

    def test_today_keyword(self):
        assert intent("what happened today") == "freshness_sensitive"

    def test_latest_keyword(self):
        assert intent("latest AI news") == "freshness_sensitive"

    def test_breaking_keyword(self):
        assert intent("breaking news in technology") == "freshness_sensitive"

    # ── Year boundary tests ───────────────────────────────────────────────────

    def test_year_2023_triggers_freshness(self):
        # First year in \b20(2[3-9]|3\d)\b range
        assert intent("best laptops 2023") == "freshness_sensitive"

    def test_year_2024_triggers_freshness(self):
        assert intent("javascript frameworks 2024") == "freshness_sensitive"

    def test_year_2029_triggers_freshness(self):
        assert intent("battery technology 2029") == "freshness_sensitive"

    def test_year_2030_triggers_freshness(self):
        # Matches \b20(3\d)\b — future decade included
        assert intent("ai predictions 2030") == "freshness_sensitive"

    def test_year_2039_triggers_freshness(self):
        assert intent("climate forecast 2039") == "freshness_sensitive"

    def test_year_2022_does_NOT_trigger_freshness(self):
        # 22 is NOT in [23-9]; 2022 is outside the recency window
        result = intent("best laptops 2022")
        assert result != "freshness_sensitive"

    def test_year_2021_does_NOT_trigger_freshness(self):
        result = intent("web development trends 2021")
        assert result != "freshness_sensitive"

    def test_old_century_year_does_NOT_trigger_freshness(self):
        result = intent("cold war history 1962")
        assert result != "freshness_sensitive"

    def test_brand_plus_earnings_today_is_freshness_not_nav(self):
        # Freshness must beat navigational even when brand name is present
        assert intent("google earnings today") == "freshness_sensitive"

    def test_brand_plus_recent_year_is_freshness_not_nav(self):
        # "github 2024" is 2 words (≤ NAV_MAX_WORDS=4) but freshness wins first
        assert intent("github 2024") == "freshness_sensitive"


# ── Navigational ──────────────────────────────────────────────────────────────

class TestNavigational:
    """Nav applies only to short queries (≤ 4 words) with brand/login/site signals."""

    def test_bare_brand_name(self):
        assert intent("github") == "navigational"

    def test_brand_with_login_action(self):
        assert intent("reddit login") == "navigational"

    def test_brand_with_download_action(self):
        assert intent("download spotify") == "navigational"

    def test_domain_pattern_in_query(self):
        # "\w+\.com" pattern
        assert intent("github.com") == "navigational"

    def test_official_site_phrase(self):
        assert intent("discord official site") == "navigational"

    def test_install_action(self):
        assert intent("install notion") == "navigational"

    # ── Brand-in-long-query edge cases ────────────────────────────────────────
    # These are the trickiest: brand name present but query is too long for nav.

    def test_brand_in_5_word_query_is_NOT_nav(self):
        # 5 words > NAV_MAX_WORDS=4 → nav check skipped entirely
        result = intent("github best practices for teams")
        assert result != "navigational"

    def test_brand_in_6_word_query_is_NOT_nav(self):
        result = intent("github deployment strategies for microservices architectures")
        assert result != "navigational"

    def test_brand_in_why_question_is_NOT_nav(self):
        # "why" deep pattern + long → deep_research
        result = intent("why is github copilot controversial among developers")
        assert result == "deep_research"

    def test_brand_in_comparison_is_NOT_nav(self):
        result = intent("compare github and gitlab for team collaboration")
        assert result == "deep_research"

    def test_brand_in_long_query_falls_to_deep_research(self):
        # 5 words, brand present, no fresh signals, word count ≥ 5 → deep_research
        result = intent("reddit alternatives for developer communities")
        assert result == "deep_research"


# ── Deep research ─────────────────────────────────────────────────────────────

class TestDeepResearch:
    """Causal, comparative, historical, and long queries without fresher signals."""

    def test_why_question(self):
        assert intent("why does inflation cause recessions") == "deep_research"

    def test_how_does_work(self):
        assert intent("how does garbage collection work in python") == "deep_research"

    def test_comparison_keyword(self):
        assert intent("rust versus c plus plus performance comparison") == "deep_research"

    def test_history_of_pattern(self):
        assert intent("history of the internet") == "deep_research"

    def test_research_keyword(self):
        assert intent("research on sleep deprivation effects") == "deep_research"

    def test_quoted_phrase_triggers_deep(self):
        assert intent('"fast inverse square root" algorithm explanation') == "deep_research"

    def test_long_query_without_explicit_signal_falls_to_deep(self):
        # 5 words, no fresh/nav/deep pattern → word-count branch → deep_research
        assert intent("open source license types overview") == "deep_research"

    def test_old_year_in_5_word_query_gives_deep_not_fresh(self):
        # 2022 is not fresh; 5 words → deep_research
        result = intent("best python frameworks 2022 comparison")
        assert result == "deep_research"

    def test_old_year_1929_in_long_query_gives_deep(self):
        # 1929 not a fresh year; 5 words with "history of" → deep_research
        result = intent("history of the 1929 market crash")
        assert result == "deep_research"

    def test_brand_in_5_word_query_gives_deep_not_nav(self):
        result = intent("github actions cost for startups")
        assert result == "deep_research"

    def test_impact_keyword(self):
        assert intent("impact of social media on democracy") == "deep_research"

    def test_controversy_keyword(self):
        assert intent("controversy surrounding cryptocurrency energy use") == "deep_research"


# ── Informational (catch-all) ─────────────────────────────────────────────────

class TestInformational:
    """Short, generic queries without any other signal fall here."""

    def test_empty_string(self):
        assert intent("") == "informational"

    def test_single_generic_word(self):
        assert intent("python") == "informational"

    def test_two_word_generic(self):
        assert intent("machine learning") == "informational"

    def test_three_word_generic(self):
        assert intent("open source software") == "informational"

    def test_four_words_no_signals(self):
        # 4 words < DEEP_MIN_WORDS=5, no fresh/nav/deep pattern → informational
        assert intent("python web development tools") == "informational"

    def test_old_year_in_short_query_is_informational(self):
        # "1929" → not fresh; 2 words → not deep (too short); no nav signal
        assert intent("recession 1929") == "informational"

    def test_old_year_2022_in_3_word_query(self):
        # 3 words, old year → informational
        result = intent("python books 2022")
        assert result == "informational"
