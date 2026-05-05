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
echo "$(date -Is) materialize sanitized huddle/standup transcripts for QMD"
python3 scripts/materialize_qmd_huddle_transcripts.py
echo "$(date -Is) materialize conversation-oriented Slack chunks for QMD"
python3 scripts/materialize_qmd_conversation_chunks.py --run-id "$RUN_ID" --workers "${SLACK_CONVERSATION_WORKERS:-1}"
qmd collection add qmd/huddle-transcripts --name huddle-transcripts 2>/dev/null || true
qmd collection add qmd/slack-conversations --name slack-conversations 2>/dev/null || true
qmd context add qmd://huddle-transcripts "Searchable markdown view over sanitized Slack huddle and standup transcripts materialized from local transcript text files. Use for meeting context and cite the qmd/huddle-transcripts path plus source_path." 2>/dev/null || true
qmd context add qmd://slack-conversations "Conversation-oriented Slack chunks for wiki ingestion: deterministic one-file Slack threads, deterministic one-file transcripts, and inferred in-channel conversations for unthreaded messages." 2>/dev/null || true
echo "$(date -Is) qmd update for lexical search"
qmd update
echo "$(date -Is) qmd collection list"
qmd collection list
echo "$(date -Is) done"
