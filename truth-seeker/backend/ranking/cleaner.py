"""
Content cleaning and commercial signal detection.
Provides compiled regex patterns and helper functions used by the scoring module.
"""
import re
from typing import List
from functools import lru_cache

# ── Affiliate / tracking link patterns ──────────────────────────────────────
AFFILIATE_PATTERNS = [
    r"amazon\.com/.*?/ref=",          # Amazon affiliate ref tags
    r"amzn\.to/",                     # Shortened Amazon affiliate
    r"shareasale\.com",
    r"clickbank\.",
    r"cj\.com",                       # Commission Junction
    r"awin\.com",
    r"impact\.com",
    r"pepperjam\.com",
    r"rakuten\.com/click",
    r"viglink\.com",
    r"skimlinks\.com",
    r"&tag=[a-z0-9\-]+",              # Generic affiliate tag param
    r"\?ref=[a-z0-9\-]+",
    r"affiliate",
    r"referral.*link",
    # Extended coverage (v2)
    r"partnerstack\.com",
    r"commission\s+junction",
    r"howl\.me",
    r"go\.redirectingat\.com",
    r"bhphotovideo\.com.*affiliate",
    r"bestbuy\.com.*affiliate",
    r"ebay\.com/itm.*mkevt=",
    r"paid\s+partnership",
    r"sponsored\s+link",
    r"#ad\b",
]

# ── SEO / listicle / commercial content patterns ─────────────────────────────
SEO_PATTERNS = [
    r"\bbest\s+\d+\b",                # "best 10 laptops"
    r"\btop\s+\d+\b",                 # "top 5 VPNs"
    r"\b\d+\s+best\b",                # "10 best tools"
    r"\breviews?\s+20\d{2}\b",        # "reviews 2024"
    r"\bbuy\s+now\b",
    r"\badd\s+to\s+cart\b",
    r"\bdiscount\s+code\b",
    r"\bcoupon\s+code\b",
    r"\bsponsored\s+(post|content|by)\b",
    r"\baffiliate\s+disclosure\b",
    r"\bthis\s+post\s+(may\s+contain|contains)\s+affiliate",
    r"\bwe\s+(earn|may\s+earn)\s+a\s+commission\b",
    r"\bcheck\s+(the\s+)?price\b",
    r"\bget\s+(the\s+)?best\s+deal\b",
    # Extended coverage (v2)
    r"\bbest\s+\w+\s+for\s+\w+\b",   # "best laptop for students"
    r"\b\d+\s+reasons?\s+(to|why)\b", # "5 reasons to buy"
    r"\bultimate\s+guide\s+to\b",
    r"\bcomprehensive\s+(guide|review|list)\b",
    r"\bbest\s+(overall|value|budget|premium|pick)\b",
    r"\beditor.?s?\s+choice\b",
    r"\bour\s+#?1\s+(pick|choice|recommendation)\b",
    r"\bwe\s+tested\b",              # "we tested 47 products"
    r"\bhands.on\s+review\b",
    r"\bpros\s+and\s+cons\b",
    r"\brating:\s*\d",
    r"\b\d+(\.\d+)?/10\b",           # "8.5/10"
    r"\bstars?\s+out\s+of\b",
]

# ── Price / transactional signals ────────────────────────────────────────────
PRICE_PATTERNS = [
    r"\$\s*\d[\d,]*(?:\.\d{2})?",     # $19.99, $1,299
    r"\d+\s*USD\b",
    r"\bstarting\s+at\s+\$",
    r"\bprice\s*:",
    r"\bfree\s+trial\b",
    r"\bsubscription\s+plan\b",
    r"\bper\s+month\b",
    # Extended (v2)
    r"€\s*\d[\d,]*",                  # Euro prices
    r"£\s*\d[\d,]*",                  # GBP prices
    r"\bmsrp\b",
    r"\bretail\s+price\b",
    r"\bregular\s+price\b",
    r"\bsale\s+price\b",
    r"\bon\s+sale\s+for\b",
    r"\bwas\s+\$\d",                  # "was $49, now $29"
    r"\bsave\s+\d+%",
]

# ── Outbound link density — injected by scorer at analysis time ──────────────
# Pattern to count raw external links in HTML (used by enhanced commercial scorer)
EXTERNAL_LINK_PATTERN = r'href=["\']https?://(?!{domain})[^"\']+["\']'

# ── Boilerplate strings (noise in content) ───────────────────────────────────
BOILERPLATE_PATTERNS = [
    r"\bcookie\s+(policy|consent|banner)\b",
    r"\bprivacy\s+policy\b",
    r"\bterms\s+of\s+(service|use)\b",
    r"\ball\s+rights\s+reserved\b",
    r"\bcopyright\s+\©?\s*\d{4}\b",
    r"\bsubscribe\s+to\s+(our\s+)?newsletter\b",
    r"\bsign\s+up\s+for\s+free\b",
    r"\bfollow\s+us\s+on\b",
    r"\bshare\s+this\s+(article|post|page)\b",
    r"\byou\s+might\s+also\s+like\b",
    r"\brelated\s+articles?\b",
    r"\bclick\s+here\s+to\b",
    r"\bread\s+more\b",
    r"\bpowered\s+by\b",
]


@lru_cache(maxsize=128)
def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def count_pattern_matches(text: str, patterns: List[str]) -> int:
    """Return total number of regex matches across all patterns."""
    total = 0
    for p in patterns:
        total += len(_compile(p).findall(text))
    return total


def strip_boilerplate(text: str) -> str:
    """Remove known boilerplate phrases and normalize whitespace."""
    for p in BOILERPLATE_PATTERNS:
        text = _compile(p).sub(" ", text)
    # Collapse whitespace
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()
