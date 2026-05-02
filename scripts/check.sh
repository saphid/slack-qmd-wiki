#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
python3 -m compileall -q scripts
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/*.sh
else
  echo "shellcheck not installed; skipped" >&2
fi
if command -v node >/dev/null 2>&1; then
  node --check scripts/qmd_search_server.mjs
else
  echo "node not installed; skipped" >&2
fi
