#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
export LLM_WIKI_SEARCH_HOST="${LLM_WIKI_SEARCH_HOST:-127.0.0.1}"
export QMD_EMBED_MODEL="${QMD_EMBED_MODEL:-hf:mixedbread-ai/mxbai-embed-large-v1/gguf/mxbai-embed-large-v1-f16.gguf}"
export QMD_LLAMA_GPU="${QMD_LLAMA_GPU:-false}"
export LLM_WIKI_SEARCH_PORT="${LLM_WIKI_SEARCH_PORT:-8765}"
exec node scripts/qmd_search_server.mjs
