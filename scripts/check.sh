#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
python3 -m compileall -q scripts
python3 scripts/smoke_huddle_transcript_ingestion.py
python3 scripts/smoke_conversation_chunking.py
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/*.sh
else
  echo "shellcheck not installed; skipped" >&2
fi
if command -v node >/dev/null 2>&1; then
  node --check scripts/qmd_search_server.mjs
  tmp_js="$(mktemp "${TMPDIR:-/tmp}/qmd-inline-js.XXXXXX.js")"
  trap 'rm -f "$tmp_js"' EXIT
  python3 - <<'PY' > "$tmp_js"
from pathlib import Path
html = Path('scripts/qmd-search.html').read_text()
start = html.index('<script>') + len('<script>')
end = html.index('</script>', start)
print(html[start:end])
PY
  node --check "$tmp_js"
  rm -f "$tmp_js"
  trap - EXIT
else
  echo "node not installed; skipped" >&2
fi
