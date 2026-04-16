# ⟁ Truth Seeker

> A local-first meta search engine that prioritises signal over noise,
> obscure over popular, and information over commerce.

Anti-echo-chamber by design: exploration crawlers actively inject results from
unknown and low-trust domains into every search. Semantic clustering prevents
ten near-identical pages from dominating the top results.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Quick Start](#quick-start)
3. [Configuration](#configuration)
4. [Accessing the UI](#accessing-the-ui)
5. [Architecture](#architecture)
6. [Ranking Engine](#ranking-engine)
7. [Crawl Pipeline](#crawl-pipeline)
8. [API Reference](#api-reference)
9. [UI Features](#ui-features)
10. [Test Suite](#test-suite)
11. [Dependencies](#dependencies)
12. [Extending](#extending)

---

## Requirements

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Python | 3.10+ | 3.13 tested |
| Node.js | 18+ | For the React frontend |
| npm | 9+ | Bundled with Node |
| Rust | any recent | Required to build `pydantic-core` from source. On Termux: `pkg install rust`. On Linux/Mac: [rustup.rs](https://rustup.rs). `start-backend.sh` installs it automatically on Termux. |

No database server required — persistence uses SQLite at `~/.truth-seeker/cache.db`.

---

## Quick Start

Clone or navigate to the project root, then open **two terminals**.

### Terminal 1 — Backend

```bash
cd truth-seeker
bash start-backend.sh
```

The script will:
- Create `backend/.venv` (Python virtualenv) on first run
- Install all Python dependencies from `requirements.txt`
- Copy `config/.env.example` → `backend/.env` if it does not exist
- Start FastAPI on **http://localhost:8000**

### Terminal 2 — Frontend

```bash
cd truth-seeker
bash start-frontend.sh
```

The script will:
- Run `npm install` on first run
- Start the Vite dev server on **http://localhost:5173**

> Both processes must be running at the same time.

### Manual Setup (if scripts fail)

```bash
# ── Backend ──────────────────────────────────────────────────
cd truth-seeker/backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
cp ../config/.env.example .env     # edit .env and add your keys
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# ── Frontend (separate terminal) ─────────────────────────────
cd truth-seeker/frontend
npm install
npm run dev
```

---

## Configuration

Edit **`backend/.env`** (created automatically from the template on first run):

```env
# Brave Search API — optional but strongly recommended
# Free tier: 2,000 queries/month
# https://api.search.brave.com
BRAVE_API_KEY=your_key_here
```

Without `BRAVE_API_KEY` the engine works using DuckDuckGo only.
With a key, results come from both sources in parallel.

---

## Accessing the UI

| URL | What it is |
|-----|-----------|
| http://localhost:5173 | Main search UI |
| http://localhost:8000/docs | FastAPI interactive API docs (Swagger) |
| http://localhost:8000/api/debug/storage | DB health stats (JSON) |

The Vite dev server proxies all `/api/*` requests to the FastAPI backend, so
the frontend always talks to `localhost:5173` — no CORS configuration needed
in the browser.

> **On Termux / Android:** replace `localhost` with `127.0.0.1` if your
> browser does not resolve `localhost` correctly.

---

## Architecture

```
truth-seeker/
├── backend/
│   ├── main.py                        App entry point + CORS middleware
│   ├── requirements.txt
│   ├── routes/
│   │   └── search.py                  POST /api/search — 15-stage pipeline
│   ├── scrapers/
│   │   ├── duckduckgo.py              DDG (no API key required)
│   │   ├── brave.py                   Brave Search API (optional)
│   │   ├── reddit.py                  Reddit (activated by forums_only)
│   │   └── wayback.py                 Wayback Machine fallback for thin pages
│   ├── ranking/
│   │   ├── engine.py                  Orchestrates scoring, link graph, clustering, blending
│   │   ├── scores.py                  6 individual scoring functions
│   │   ├── anti_seo.py                AI-spam / title-stuffing detector
│   │   ├── cleaner.py                 Regex patterns for commercial detection
│   │   ├── query_intent.py            classify_query_intent() — 4 intent classes
│   │   ├── clustering.py              Content-type classifier (Forum/Blog/Academic/…)
│   │   ├── blending.py                blend_for_diversity() — domain cap + exploration injection
│   │   ├── link_graph.py              Inbound link authority boost (Phase 6b)
│   │   └── semantic_clustering.py     TF-IDF cosine clustering + diversify_top_10 (Phase 6a)
│   ├── crawler/
│   │   ├── crawl_budget.py            Shared page budget across all crawlers
│   │   ├── micro_crawler.py           Deep-crawl top seeds (deep_crawl toggle)
│   │   ├── seed_expander.py           Follow outbound links (seed_expand toggle)
│   │   ├── domain_explorer.py         Crawl high-trust domains (domain_explore toggle)
│   │   └── link_filter.py             Filters low-value crawl targets
│   ├── cache/
│   │   ├── page_cache.py              SQLite page store — BM25 search, domain trust memory
│   │   └── query_memory.py            Query history — fuzzy recall, search_count tracking
│   ├── utils/
│   │   ├── extractor.py               Async page fetching + trafilatura extraction
│   │   ├── dedup.py                   URL normalisation + fuzzy title dedup
│   │   └── query_expander.py          Query variant generation
│   └── tests/
│       ├── test_query_intent.py       43 unit tests — intent classifier edge cases
│       ├── test_blending.py           17 unit tests — domain cap + exploration injection
│       ├── test_semantic_clustering.py 16 unit tests — clustering invariants
│       ├── test_phase_6.py            19 unit tests — link graph + query memory
│       └── test_search_integration.py Integration tests — full pipeline wiring
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── styles.css
│   │   └── components/
│   │       ├── SearchBar.jsx          Query input + crawl toggles
│   │       ├── FilterPanel.jsx        Sliders + hard filter toggles
│   │       ├── ResultsList.jsx        Pipeline stats + intent badge + trust bar
│   │       └── ResultCard.jsx         Per-result score breakdown + cluster label
│   ├── vite.config.js                 Proxies /api → :8000
│   └── package.json
├── config/
│   └── .env.example
├── start-backend.sh
└── start-frontend.sh
```

### Data flow

```
Query
  │
  ├─ classify_query_intent()        (navigational / deep_research / freshness_sensitive / informational)
  ├─ variants_for_scraping()        (up to 3 expanded query variants)
  │
  ├─── DDG scraper ──┐
  ├─── Brave scraper ─┤  parallel
  ├─── Reddit scraper ┤  gather
  └─── Cache search ─┘
          │
     deduplicate_results()
          │
     extract_content_batch()        (trafilatura, async)
          │
     wayback_fallback_batch()       (fill thin pages from Wayback Machine)
          │
     rank_results() ── preliminary  (intent-adaptive weights + domain trust)
          │
     CrawlBudget()                  (shared page budget, intent-tuned)
          │
  ┌──────┴──────┐
  │  [optional] │
  ├─ crawl_deeper()                 (deep_crawl=true)
  ├─ expand_from_seeds()            (seed_expand=true)
  └─ explore_high_score_domains()   (domain_explore=true → is_exploration=True tags)
          │
     rank_results() ── final        (all sources combined)
       ├─ link_graph boost          (inbound authority, ±0.05)
       ├─ semantic_clustering()     (TF-IDF cosine, diversify top 10)
       └─ blend_for_diversity()     (domain cap ≤2, ≥2 exploration in top 10)
          │
     _apply_filters()              (corporate, forums_only, long_form_only)
          │
     cache.store_batch()           (quality-gated, fire-and-forget)
     query_memory.log_successful_query()
          │
     Response JSON
```

---

## Ranking Engine

### Scoring Formula (v4)

```
final = (info_density  × w1)
      + (obscurity      × w2)
      − (commercial_bias × w3)
      + (freshness      × w4)
      + (diversity      × w5)
      − (ai_spam        × w6)
      + domain_trust_adj          ← ±0.04  (from cache domain memory)
      + link_graph_boost          ← 0–0.05 (from inbound authority)
      − over_optimisation_penalty ← 0–0.12 (keyword stuffing in title)
      − duplicate_penalty         ← 0.10   (near-duplicate fingerprint)
```

Default weights: `w1=0.26, w2=0.22, w3=0.20, w4=0.09, w5=0.09, w6=0.14`

### Score Dimensions

| Dimension | What it measures | Effect |
|-----------|-----------------|--------|
| **Info Density** | Word count, lexical diversity, sentence length | + added |
| **Obscurity** | Source independence; penalises mainstream domains | + added |
| **Commercial Bias** | Affiliate links, price signals, SEO patterns | − subtracted |
| **Freshness** | Exponential decay from publish date (τ = 365 days) | + added |
| **Diversity** | Penalises repeated domains across the result set | + added |
| **AI Spam** | Title stuffing, keyword repetition, template patterns | − subtracted |

### Intent-Adaptive Weights

`classify_query_intent()` analyses the query before scraping and shifts weights:

| Intent | Trigger | Weight shifts |
|--------|---------|---------------|
| `freshness_sensitive` | "today", "latest", year 2023–2039 | freshness +0.15, obscurity −0.05 |
| `deep_research` | "why", "how does", "compare", quoted phrases, 5+ words | obscurity +0.08, info_density +0.05 |
| `navigational` | Brand name, ≤4 words, "login", domain pattern | diversity +0.05, obscurity −0.08 |
| `informational` | Everything else | No shift (default weights) |

> Freshness always beats navigational beats deep_research beats informational.
> The navigational check is skipped entirely for queries longer than 4 words —
> a brand name in a long query never produces `navigational`.

### Anti-Gaming Layers

- **Over-optimisation penalty** — if ≥80% of query terms appear verbatim in the
  title and the title is ≥6 words long, subtract up to 0.12 from final score.
- **Near-duplicate penalty** — MD5 fingerprint of the first 300 words; second
  occurrence of the same fingerprint loses 0.10 points.
- **AI/spam detection** — `anti_seo.py` detects template language, excessive
  keyword density, and listicle patterns.

### Semantic Clustering (Phase 6a)

After preliminary scoring, the top 50 results are clustered by snippet
similarity using TF-IDF cosine distance + agglomerative clustering (pure
numpy, no scipy required). `diversify_top_10()` round-robin picks one result
per cluster to build the top 10, preventing topic repetition. Each result
carries `cluster_id` and `cluster_label` (auto-generated from top content nouns).

Uses `sentence-transformers/all-MiniLM-L6-v2` for embedding if the package
is installed; falls back to TF-IDF automatically.

### Link Graph Authority (Phase 6b)

When crawlers are active (`domain_explore`, `seed_expand`), result dicts
contain `_outbound_links`. For each domain in the top 50, the engine counts
which other results link to it and sums the trust scores of those sources:

```
boost = Σ trust[linking_domain] × 0.02    (capped at +0.05)
```

Unknown domains default to neutral trust (0.5). Link count per source is
intentionally ignored — only unique linking domains contribute — so a single
domain cannot game the signal by linking many times.

---

## Crawl Pipeline

### Crawl Budget

All three optional crawlers share a single `CrawlBudget` (default 25 pages
per request). The budget is intent-tuned:

- `deep_research` → exploration ratio +30%, depth multiplier +50%
- `freshness_sensitive` → freshness weight +20%
- `navigational` → focused on top-2 seeds only

### Crawl Modes (all optional, all toggleable per request)

| Toggle | What it does |
|--------|-------------|
| `deep_crawl` | Micro-crawls top 7 seeds — follows internal links 1–2 levels deep |
| `seed_expand` | Follows outbound links from enriched results — discovers adjacent pages |
| `domain_explore` | Crawls high-trust domains not recently visited + injects low-trust "unknown" domains as `is_exploration=True` results (anti-echo-chamber) |

### Exploration & Anti-Bias

Results tagged `is_exploration=True` come from domains outside the mainstream
trust graph — independent sites, low-traffic blogs, small communities. The
blending layer (`blend_for_diversity`) guarantees at least 2 such results
appear in the top 10 when they are available. They are labelled **◈ anti-bias**
in the UI.

### Persistent Cache

SQLite at `~/.truth-seeker/cache.db`. Tables:

| Table | Purpose |
|-------|---------|
| `pages` | Scored metadata + truncated content |
| `pages_fts` | FTS5 BM25 full-text index |
| `domain_stats` | Per-domain quality memory (avg_score, last_seen, exploration history) |
| `query_memory` | Query history with intent + search_count (Phase 6c) |

**Quality gates** — pages are only stored if:
- `final_score ≥ 0.60`
- `ai_spam < 0.65`
- `commercial_bias < 0.75`
- `word_count ≥ 80`

**Tiered TTL:**
- score ≥ 0.85 → 30 days
- score ≥ 0.70 → 7 days
- score < 0.70 → 3 days

**Domain trust memory** — `domain_stats.avg_score` decays toward 0.5 (neutral)
based on days since last seen (7 days = no decay; >90 days = 65% pull toward
neutral). This trust score feeds the ranking engine's `domain_trust_adj`.

### Query Memory (Phase 6c)

Every successful search is logged to `query_memory`. `get_similar_queries()`
uses `difflib.SequenceMatcher` + substring matching to recall past queries —
useful for surfacing intent patterns and future personalisation.

---

## API Reference

### `POST /api/search`

**Request body:**

```json
{
  "query": "string",

  // Sliders (0.0–1.0, default 0.5)
  "underground_bias": 0.5,    // 0=mainstream  1=obscure/independent
  "freshness_bias":   0.5,    // 0=archived    1=recent

  // Hard filters
  "exclude_corporate": false, // remove big corporate domains
  "forums_only":       false, // keep only forum/discussion pages
  "long_form_only":    false, // require word_count > 800

  // Crawl toggles
  "deep_crawl":     false,    // micro-crawl top seeds
  "seed_expand":    false,    // follow outbound links
  "domain_explore": false,    // explore + inject anti-bias results

  // Quality toggles
  "deseo_mode":       false,  // aggressive AI/SEO penalty
  "forums_priority":  false,  // boost forum results in ranking
  "use_cache":        true,   // read from + write to local cache

  "max_results": 20           // 1–50
}
```

**Response:**

```json
{
  "query": "...",
  "total": 18,

  // Phase 5 observability
  "intent": "deep_research",
  "exploration_results_count": 2,
  "exploration_used": 3,
  "trust_distribution": { "high": 5, "medium": 8, "low": 5 },

  // Pipeline stats
  "sources_used":          { "ddg": 7, "brave": 6, "cache": 5 },
  "deep_crawl_pages":      0,
  "seed_expanded_pages":   0,
  "domain_explored_pages": 2,
  "cache_hits":            5,
  "saved_pages_total":     142,
  "query_variants":        2,
  "crawl_budget":          { "used": 2, "remaining": 23, "... ": "..." },
  "trusted_domains":       31,

  "results": [
    {
      "title":   "...",
      "url":     "https://example.org/article",
      "snippet": "...",
      "domain":  "example.org",
      "source":  "ddg",
      "word_count":   2400,
      "publish_date": "2024-03-01",
      "author":       "Jane Smith",
      "from_cache":   false,
      "is_exploration": false,

      // Phase 6a
      "cluster_id":    2,
      "cluster_label": "semantic language model",

      // Phase 6b
      "trust_boost": 0.02,

      "cluster": "Blog",
      "scores": {
        "final":           0.712,
        "info_density":    0.831,
        "obscurity":       0.650,
        "commercial_bias": 0.031,
        "freshness":       0.582,
        "diversity":       1.000,
        "ai_spam":         0.041
      },
      "weights_used": {
        "info_density":    0.2812,
        "obscurity":       0.2812,
        "commercial_bias": 0.20,
        "freshness":       0.09,
        "diversity":       0.09,
        "ai_spam":         0.14
      },
      "anti_seo_detail": { "... ": "..." }
    }
  ]
}
```

### `GET /api/debug/storage`

Returns DB health stats:

```json
{
  "total_pages":      142,
  "total_domains":    87,
  "high_value_pages": 23,
  "avg_score":        0.681,
  "oldest_cached":    1711234567.0,
  "db_size_mb":       1.4,
  "db_path":          "/root/.truth-seeker/cache.db"
}
```

### `POST /api/debug/cleanup`

Manually triggers TTL expiry and size-cap trim. Returns `{"expired": N, "trimmed": N}`.

Interactive docs (Swagger UI): **http://localhost:8000/docs**

---

## UI Features

### Search Bar

- Query input with instant submit on Enter
- Per-search toggles: **Deep Crawl**, **Seed Expand**, **Domain Explore**
- Slider panel: underground bias, freshness bias, De-SEO mode, forums priority

### Intent Badge

Displayed next to the query when intent is non-default:

| Badge | Intent | Colour |
|-------|--------|--------|
| `⊙ FRESH` | freshness_sensitive | Yellow |
| `⊙ RESEARCH` | deep_research | Purple |
| `⊙ NAVIGATE` | navigational | Cyan |
| *(hidden)* | informational | — |

### Pipeline Stats Bar

Appears above results:

- Result count + source breakdown
- **◈ N explored** chip — count of anti-bias results injected (purple, only shown when > 0)
- **TRUST** segmented bar — proportional green/yellow/red bar showing high/medium/low domain trust distribution across results

### Result Cards

Each card shows:
- Title, URL, snippet, domain, source, publish date
- **◈ anti-bias** pill (purple) — marks `is_exploration=True` results
- **🔍 explored** pill — marks results found by crawlers
- Content-type cluster label (Forum / Blog / Academic / News / Docs / Wiki / Commercial / Unknown)
- Semantic cluster label (`cluster_label`) from Phase 6a
- Expandable score breakdown: all 6 signal scores + weights used

---

## Test Suite

Run all unit tests from `backend/`:

```bash
cd truth-seeker/backend
python3 -m pytest tests/ -v
```

| File | Tests | Covers |
|------|-------|--------|
| `test_query_intent.py` | 43 | Intent classifier — year boundaries, brand-in-long-query, priority ordering |
| `test_blending.py` | 17 | Domain cap (top-N scoped), exploration injection with fewer candidates than deficit |
| `test_semantic_clustering.py` | 16 | Cluster invariants, identical/distinct snippets, diversify round-robin |
| `test_phase_6.py` | 19 | Link graph counting + boost formula, query memory log/recall/sort |
| `test_search_integration.py` | 9 | Full pipeline wiring (requires FastAPI in active Python env) |

**Total: 95 unit tests (all passing)**

To run a single file:

```bash
python3 -m pytest tests/test_blending.py -v
```

The integration tests require FastAPI to be importable. If running outside the
`.venv`, activate it first:

```bash
source backend/.venv/bin/activate
python3 -m pytest tests/test_search_integration.py -v
```

---

## Dependencies

### Backend (`requirements.txt`)

| Package | Purpose |
|---------|---------|
| `fastapi` | API framework |
| `uvicorn[standard]` | ASGI server |
| `httpx` | Async HTTP client |
| `trafilatura` | Article extraction + metadata |
| `beautifulsoup4` + `lxml` | HTML parsing fallback |
| `tldextract` | Domain parsing |
| `duckduckgo-search` 5.x | DDG results via HTML endpoint (no key) |
| `pydantic` | Request/response validation |
| `python-dotenv` | `.env` loading |

`numpy` is included in `requirements.txt` and used by `semantic_clustering.py`. `sentence-transformers`
is optional — install it for higher-quality semantic embeddings:

```bash
pip install sentence-transformers
```

Without it, the clustering falls back to TF-IDF cosine similarity automatically.

### Frontend

| Package | Purpose |
|---------|---------|
| `react` 18 | UI framework |
| `vite` 5 | Dev server + bundler |
| `@vitejs/plugin-react` | JSX transform |

Zero runtime dependencies beyond React itself.

---

## Extending

### Add a new search source

1. Create `backend/scrapers/mysource.py` returning a list of dicts with keys:
   `url, domain, title, snippet, source, word_count, publish_date`
2. Import and `asyncio.create_task()` it in `routes/search.py` alongside the
   existing scraper tasks.

### Add a new ranking signal

1. Add a scoring function to `ranking/scores.py` returning a float in `[0, 1]`.
2. Wire it into `rank_results()` in `ranking/engine.py`.
3. Add the score to the `scores` dict and the weight to `weights_used`.
4. Display it in `frontend/src/components/ResultCard.jsx`.

### Persistent semantic embeddings

`semantic_clustering.py` currently computes embeddings per-request and discards
them. To cache embeddings, add a `snippet_hash → embedding BLOB` column to the
`pages` table in `page_cache.py` and load them in `cluster_results()` before
calling `_embed_batch()`.

### Install sentence-transformers for better clustering

On systems with pip access to PyTorch (not Android/Termux):

```bash
source backend/.venv/bin/activate
pip install sentence-transformers
```

The model (`all-MiniLM-L6-v2`, ~80 MB) is downloaded on first use and cached
by the library in `~/.cache/huggingface/`. No code changes needed — the module
auto-detects the package at runtime.
