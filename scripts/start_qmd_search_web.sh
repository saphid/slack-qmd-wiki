#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
export LLM_WIKI_SEARCH_HOST="${LLM_WIKI_SEARCH_HOST:-127.0.0.1}"
export LLM_WIKI_SEARCH_PORT="${LLM_WIKI_SEARCH_PORT:-8765}"
exec node scripts/qmd_search_server.mjs
