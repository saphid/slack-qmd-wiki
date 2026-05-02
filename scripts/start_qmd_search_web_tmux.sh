#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
session="${LLM_WIKI_SEARCH_SESSION:-llm-wiki-qmd-search-web}"
mkdir -p .state/qmd
if tmux has-session -t "$session" 2>/dev/null; then
  echo "already running: $session"
else
  log=".state/qmd/qmd-search-web-$(date -u +%Y%m%dT%H%M%SZ).log"
  ln -sfn "$(basename "$log")" .state/qmd/qmd-search-web.latest.log
  tmux new-session -d -s "$session" "./scripts/start_qmd_search_web.sh >\"$log\" 2>&1"
  echo "started: $session"
  echo "log: $log"
fi
echo "url: http://${LLM_WIKI_SEARCH_HOST:-127.0.0.1}:${LLM_WIKI_SEARCH_PORT:-8765}"
