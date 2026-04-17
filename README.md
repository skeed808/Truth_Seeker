# Truth Seeker — Open-Source Search Engine for Buried Information

A locally-hosted, ad-free search engine designed to surface high-signal information 
that mainstream engines bury or suppress. Combines meta-search aggregation with 
intelligent ranking, semantic analysis, link graph authority, temporal intelligence, 
and persistent user feedback to discover diverse perspectives.

## Why Truth Seeker

Google optimizes for engagement and advertising revenue.
Truth Seeker optimizes for **signal**.

- **Anti-SEO ranking**: Commercial bias penalty, AI-spam detection, title-stuffing 
  signals, over-optimization penalties prioritize genuine knowledge over marketing
- **Semantic clustering**: Results grouped by *meaning*, not keyword overlap—see 
  multiple perspectives on the same topic
- **Link graph analysis**: Tracks inbound citations to identify emerging authorities 
  missed by traditional trust metrics
- **Temporal intelligence**: Detects when information trends, gets suppressed, or 
  resurfaces—show *when* suppression happens
- **User feedback loop**: Thumbs-up/down voting influences ranking for future searches
- **Cache-first search**: Build your own local knowledge base; APIs become optional 
  fallback
- **Transparent scoring**: Every result shows *why* it ranked where—no black-box 
  ranking
- **100% offline**: Works without internet; no tracking, no profiling, no shadow banning

## Key Features

### Phases Completed (1–9)
- **Phase 1–4**: Core pipeline, anti-SEO signals, SQLite cache, micro-crawler
- **Phase 5**: Query intent classification, anti-bias exploration pools
- **Phase 6**: Semantic clustering, link graph authority boost, query memory
- **Phase 7**: User feedback voting with ranking influence
- **Phase 8**: Temporal signals (trend detection, recency momentum, burst analysis)
- **Phase 9**: Cache-first search with offline mode and refresh capability

### Tech Stack
- **Backend**: FastAPI, SQLite, sentence-transformers
- **Frontend**: React + Vite
- **Data sources**: DuckDuckGo (required), Brave Search API (optional)
- **No external infrastructure**—runs fully local

### Response Transparency
Every result includes:
- Intent classification (navigational/informational/deep research/freshness-sensitive)
- Semantic cluster label (topic grouping)
- Link graph trust boost (+/-0.05)
- Temporal signals (freshness score, burst detection, recency momentum)
- User feedback influence
- Full score breakdown (information density, obscurity, commercial bias, etc.)
- Source badge (cache/hybrid/API/refreshed)

## Quick Start

```bash
git clone https://github.com/skeed808/Truth_Seeker
cd Truth_Seeker

# Terminal 1 — Backend
bash start-backend.sh

# Terminal 2 — Frontend
bash start-frontend.sh
