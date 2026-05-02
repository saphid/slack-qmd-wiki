#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
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
echo "$(date -Is) materialize completed Slack chunks for QMD"
python3 scripts/materialize_qmd_slack_chunks.py --run-id "$RUN_ID" --only-complete
echo "$(date -Is) qmd update for lexical search"
qmd update
echo "$(date -Is) qmd collection list"
qmd collection list
echo "$(date -Is) done"
