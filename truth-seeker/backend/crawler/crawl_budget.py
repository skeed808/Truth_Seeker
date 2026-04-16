"""
CrawlBudget v2 — Adaptive, Exploration-Aware Resource Allocator.

═══════════════════════════════════════════════════════════════════════════════
KEY CONCEPTS
═══════════════════════════════════════════════════════════════════════════════

1. Split budget pools
   The total crawl budget is divided into two separate pools:

     trust_pool    — allocated by trust tier (high-trust → more pages)
     explore_pool  — reserved for DELIBERATE EXPLORATION; ignores trust tiers
                     entirely, targeting unknown and low-trust domains.

   This guarantees that exploration always runs, even when the result set is
   dominated by high-trust domains that would otherwise consume everything.

2. Soft trust floor (no hard censorship)
   Every domain gets at minimum 1 page from the trust pool, regardless of its
   trust score.  Low-trust domains are *deprioritised*, not excluded.
   depth_limit() always returns ≥ 1.

3. Intent-adaptive profiles
   The exploration ratio and depth multiplier are adjusted based on query intent:

     deep_research     → exploration_ratio=0.35, depth_mult=1.4  (cast wide)
     freshness_sensitive → exploration_ratio=0.15, depth_mult=0.8 (focus recent)
     navigational      → exploration_ratio=0.05, depth_mult=0.6  (stay on target)
     informational     → exploration_ratio=0.20, depth_mult=1.0  (balanced)

4. Exploration tagging
   Domains fetched via the explore_pool are tracked in _exploration_domains.
   Crawlers mark results from these domains with is_exploration=True so the
   blending layer can ensure they surface in the final result set.

5. Pre-allocation semantics
   allocate() and allocate_exploration() decrement their respective pools
   immediately.  asyncio is single-threaded so there are no data races, but
   pre-allocation makes budget consumption explicit and predictable.

═══════════════════════════════════════════════════════════════════════════════
USAGE
═══════════════════════════════════════════════════════════════════════════════

    budget = CrawlBudget(
        total=25,
        domain_trust=domain_trust_map,  # {domain: decayed_score}
        intent="deep_research",
    )

    # Trust-based allocation:
    alloc = budget.allocate("example.com", want=4)

    # Exploration allocation (ignores trust):
    expl_candidates = budget.get_exploration_candidates(all_link_candidates)
    for c in expl_candidates:
        alloc = budget.allocate_exploration(domain, want=1)
        if alloc:
            result = await fetch(domain)
            result["is_exploration"] = True

    # Check if a domain was explored via the exploration pool:
    result["is_exploration"] = budget.is_exploration_domain(domain)
"""
import random
from typing import Dict, List, Optional
from urllib.parse import urlparse

# ── Trust tier depth limits (pages per domain) ───────────────────────────────
DEPTH_HIGH    = 5    # trust ≥ 0.75
DEPTH_MEDIUM  = 3    # trust ≥ 0.55
DEPTH_LOW     = 1    # trust ≥ 0.35  — was SKIP in v1, now soft floor
DEPTH_FLOOR   = 1    # absolute minimum for ALL domains (soft trust floor)
DEFAULT_TRUST = 0.50 # trust for domains not in the map

# ── Intent profiles ───────────────────────────────────────────────────────────
_INTENT_PROFILES: Dict[str, Dict] = {
    "deep_research":      {"exploration_ratio": 0.35, "depth_mult": 1.4},
    "freshness_sensitive":{"exploration_ratio": 0.15, "depth_mult": 0.8},
    "navigational":       {"exploration_ratio": 0.05, "depth_mult": 0.6},
    "informational":      {"exploration_ratio": 0.20, "depth_mult": 1.0},
}

# Trust threshold below which a domain qualifies for exploration sampling
_EXPLORABLE_TRUST_CEILING = 0.50


def _domain_from(c: dict) -> str:
    """Extract domain from a dict with a 'domain' or 'url' key."""
    try:
        return c.get("domain") or urlparse(c.get("url", "")).netloc.lstrip("www.")
    except Exception:
        return ""


class CrawlBudget:
    """
    Per-request crawl resource manager.

    Create one per search request; discard after the request completes.
    Not thread-safe — asyncio is single-threaded, no locking needed.
    """

    def __init__(
        self,
        total: int,
        domain_trust: Dict[str, float] = None,
        intent: str = "informational",
        exploration_ratio: Optional[float] = None,  # override profile default
    ):
        profile    = _INTENT_PROFILES.get(intent, _INTENT_PROFILES["informational"])
        ratio      = exploration_ratio if exploration_ratio is not None else profile["exploration_ratio"]

        self.total        = total
        self.intent       = intent
        self._trust       = domain_trust or {}
        self._depth_mult  = profile["depth_mult"]

        # Separate pools — exploration is always reserved
        self.explore_pool          = max(1, int(total * ratio))
        self.trust_pool            = total - self.explore_pool
        self._trust_remaining:int  = self.trust_pool
        self._explore_remaining:int = self.explore_pool

        self._used:               Dict[str, int] = {}
        self._exploration_domains: set           = set()

    # ── Derived state ─────────────────────────────────────────────────────────

    @property
    def remaining(self) -> int:
        """Total pages remaining across both pools."""
        return self._trust_remaining + self._explore_remaining

    # ── Trust accessors ───────────────────────────────────────────────────────

    def trust(self, domain: str) -> float:
        """Trust score for a domain; unknown domains receive DEFAULT_TRUST."""
        return self._trust.get(domain, DEFAULT_TRUST)

    def depth_limit(self, domain: str) -> int:
        """
        Per-domain page cap from the trust pool.

        Soft floor: every domain gets at least DEPTH_FLOOR pages, even if
        trust < 0.35.  Low-trust domains are deprioritised, not silenced.
        Depth is also scaled by the intent profile's depth_mult.
        """
        t = self.trust(domain)
        if t >= 0.75:
            base = DEPTH_HIGH
        elif t >= 0.55:
            base = DEPTH_MEDIUM
        else:
            base = DEPTH_LOW   # includes formerly-skipped < 0.35 domains

        return max(DEPTH_FLOOR, round(base * self._depth_mult))

    # ── Trust-pool accounting ─────────────────────────────────────────────────

    def can_fetch(self, domain: str) -> bool:
        """True if trust_pool has capacity and domain hasn't hit its depth limit."""
        if self._trust_remaining <= 0:
            return False
        return self._used.get(domain, 0) < self.depth_limit(domain)

    def allocate(self, domain: str, want: int) -> int:
        """
        Pre-allocate up to `want` pages for `domain` from the trust pool.
        Returns actual pages granted (may be 0 if pool or domain limit exhausted).
        """
        if self._trust_remaining <= 0 or want <= 0:
            return 0
        already  = self._used.get(domain, 0)
        allowed  = max(0, self.depth_limit(domain) - already)
        actual   = min(want, allowed, self._trust_remaining)
        if actual > 0:
            self._used[domain]    = already + actual
            self._trust_remaining -= actual
        return actual

    def consume(self, domain: str, n: int = 1) -> None:
        """Record n pages fetched without pre-allocation (trust pool)."""
        self._used[domain] = self._used.get(domain, 0) + n
        self._trust_remaining = max(0, self._trust_remaining - n)

    # ── Exploration-pool accounting ───────────────────────────────────────────

    def allocate_exploration(self, domain: str, want: int = 1) -> int:
        """
        Reserve up to `want` slots from the exploration pool for `domain`.

        Exploration ignores trust tiers entirely — this is deliberately
        targeting less-known territory.  Returns actual slots granted.
        """
        if self._explore_remaining <= 0 or want <= 0:
            return 0
        # Limit per domain in exploration: 1 page by default (probe, not crawl)
        explore_per_domain = max(1, round(DEPTH_LOW * self._depth_mult))
        already_explore = self._used.get(f"__expl__{domain}", 0)
        allowed  = max(0, explore_per_domain - already_explore)
        actual   = min(want, allowed, self._explore_remaining)
        if actual > 0:
            self._used[f"__expl__{domain}"] = already_explore + actual
            self._explore_remaining -= actual
            self._exploration_domains.add(domain)
        return actual

    def can_explore(self, domain: str) -> bool:
        """True if the exploration pool has capacity for this domain."""
        if self._explore_remaining <= 0:
            return False
        return self._used.get(f"__expl__{domain}", 0) < 1

    def is_exploration_domain(self, domain: str) -> bool:
        """Was this domain fetched via the exploration pool?"""
        return domain in self._exploration_domains

    # ── Exploration candidate selection ──────────────────────────────────────

    def get_exploration_candidates(
        self,
        candidates: List[dict],
        max_n: int = 4,
    ) -> List[dict]:
        """
        Pick a RANDOM subset of candidates from unknown or low-trust domains.

        Randomness is the key mechanism for echo-chamber breaking — the same
        high-scoring unknown domain shouldn't always be chosen.  Different
        queries should explore different corners of the web.

        Args:
            candidates: Link dicts with 'url' and/or 'domain' keys.
            max_n:      Maximum number of exploration candidates to return.

        Returns:
            Shuffled list of up to max_n candidates from explorable domains.
        """
        explorable = [
            c for c in candidates
            if self._qualifies_for_exploration(_domain_from(c))
        ]
        # Shuffle to ensure diversity across queries
        random.shuffle(explorable)
        return explorable[:max_n]

    def _qualifies_for_exploration(self, domain: str) -> bool:
        """
        True for domains that are good exploration targets:
        - Not in trust map (completely unknown)
        - OR in trust map with score < threshold (low-trust, worth probing)
        """
        if not domain:
            return False
        return domain not in self._trust or self._trust[domain] < _EXPLORABLE_TRUST_CEILING

    # ── Sorting helpers ───────────────────────────────────────────────────────

    def sort_domains(self, domains: List[str]) -> List[str]:
        """Sort domain strings by trust score, highest first."""
        return sorted(domains, key=lambda d: self.trust(d), reverse=True)

    def sort_candidates(self, candidates: List[dict]) -> List[dict]:
        """
        Sort link-candidate dicts by (link_score × trust_multiplier), descending.

        Trust multiplier: 0.35 trust → 0.70×; 0.50 → 1.00×; 1.00 → 1.50×
        Formula: priority = link_score × (0.7 + trust)
        """
        def priority(c: dict) -> float:
            t = self.trust(_domain_from(c))
            return c.get("score", 0.5) * (0.70 + t)
        return sorted(candidates, key=priority, reverse=True)

    def filter_feasible(self, candidates: List[dict]) -> List[dict]:
        """Remove candidates whose domains have hit their trust-pool cap."""
        return [c for c in candidates if self.can_fetch(_domain_from(c))]

    # ── Status ────────────────────────────────────────────────────────────────

    def summary(self) -> Dict:
        return {
            "budget_total":          self.total,
            "budget_remaining":      self.remaining,
            "trust_pool_remaining":  self._trust_remaining,
            "explore_pool_total":    self.explore_pool,
            "explore_pool_remaining":self._explore_remaining,
            "explore_pool_used":     self.explore_pool - self._explore_remaining,
            "intent":                self.intent,
            "exploration_domains":   sorted(self._exploration_domains),
            "per_domain":            {k: v for k, v in self._used.items()
                                      if not k.startswith("__expl__")},
        }
