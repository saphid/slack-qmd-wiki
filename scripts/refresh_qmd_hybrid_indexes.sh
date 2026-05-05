#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
export QMD_LLAMA_GPU="${QMD_LLAMA_GPU:-false}"
export QMD_EMBED_MODEL="${QMD_EMBED_MODEL:-hf:mixedbread-ai/mxbai-embed-large-v1/gguf/mxbai-embed-large-v1-f16.gguf}"
default_slack_run_id() {
  python3 - <<'PY'
import json, pathlib
state = pathlib.Path('.state/slack-chunk-download-state.json')
if state.exists():
    try:
        print(json.loads(state.read_text()).get('run_id') or 'all-feeds')
        raise SystemExit
    except Exception:
        pass
print('all-feeds')
PY
}
RUN_ID="${SLACK_RUN_ID:-$(default_slack_run_id)}"
LOG_PREFIX="$(date -Is)"

echo "$LOG_PREFIX materializing downloaded Slack JSON chunks for QMD markdown indexing"
python3 scripts/materialize_qmd_slack_chunks.py --run-id "$RUN_ID" --only-complete

echo "$(date -Is) ensuring QMD collections"
qmd collection add raw/slack --name slack-raw 2>/dev/null || true
qmd collection add qmd/slack-api-chunks --name slack-api-chunks 2>/dev/null || true
qmd collection add wiki --name llm-wiki 2>/dev/null || true

qmd context add qmd://slack-raw "Raw Slack markdown exports. Use for source-cited original wording where files already exist under raw/slack." 2>/dev/null || true
qmd context add qmd://slack-api-chunks "Searchable markdown view over raw Slack API JSON chunks downloaded under chunks/slack. This is closest to the raw Slack data and includes source_json pointers back to the exact API page files." 2>/dev/null || true
qmd context add qmd://llm-wiki "Compiled llm-wiki pages synthesized from raw Slack sources. Prefer for durable summaries; verify facts against slack-raw or slack-api-chunks." 2>/dev/null || true

echo "$(date -Is) qmd update"
qmd update

echo "$(date -Is) qmd embed for hybrid/vector search"
qmd embed --max-docs-per-batch "${QMD_MAX_DOCS_PER_BATCH:-200}" --max-batch-mb "${QMD_MAX_BATCH_MB:-64}"

echo "$(date -Is) qmd status"
qmd status

echo "$(date -Is) restarting qmd HTTP MCP daemon"
qmd mcp stop >/dev/null 2>&1 || true
qmd mcp --http --daemon || true
qmd status
