#!/usr/bin/env bash
# ── Truth Seeker — Backend Startup ──────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$SCRIPT_DIR/backend"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Truth Seeker — Backend"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$BACKEND"

# Create .env if it doesn't exist
if [ ! -f .env ]; then
  cp "$SCRIPT_DIR/config/.env.example" .env
  echo "⚠  Created .env from template. Add your BRAVE_API_KEY if you have one."
fi

# ── Rust is required to build pydantic-core from source ──────────────────────
# On Termux, install via: pkg install rust
# On Linux/Mac, install via: https://rustup.rs
if ! command -v cargo &>/dev/null; then
  echo ""
  echo "⚠  Rust compiler not found. pydantic-core requires Rust to build."
  if command -v pkg &>/dev/null; then
    echo "→ Installing Rust via Termux pkg..."
    pkg install -y rust
  else
    echo "   Install Rust from https://rustup.rs and re-run this script."
    exit 1
  fi
fi

# Create and activate virtual environment if needed
if [ ! -d .venv ]; then
  echo "→ Creating Python virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install dependencies
echo "→ Installing dependencies (pydantic-core builds from source — takes ~2 min on first run)..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ── Patch duckduckgo_search for Python 3.13 compatibility ────────────────────
# duckduckgo_search 5.x uses `f"{resp.elapsed:.2f}"` which fails on Python 3.13
# because elapsed is a timedelta, not a float. Patch it in-place.
DDG_ASYNC="$BACKEND/.venv/lib/python3.13/site-packages/duckduckgo_search/duckduckgo_search_async.py"
if [ -f "$DDG_ASYNC" ]; then
  if grep -q 'resp.elapsed:.2f' "$DDG_ASYNC" 2>/dev/null; then
    sed -i 's/resp\.elapsed:\.2f/resp.elapsed.total_seconds():.2f/g' "$DDG_ASYNC"
    echo "→ Patched duckduckgo_search for Python 3.13 compatibility."
  fi
fi

echo "→ Starting FastAPI server on http://localhost:8000"
echo "  API docs: http://localhost:8000/docs"
echo ""

uvicorn main:app --reload --host 0.0.0.0 --port 8000
