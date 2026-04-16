#!/usr/bin/env bash
# ── Truth Seeker — Frontend Startup ─────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND="$SCRIPT_DIR/frontend"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Truth Seeker — Frontend"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd "$FRONTEND"

if [ ! -d node_modules ]; then
  echo "→ Installing npm packages..."
  npm install
fi

echo "→ Starting Vite dev server on http://localhost:5173"
echo ""

npm run dev
