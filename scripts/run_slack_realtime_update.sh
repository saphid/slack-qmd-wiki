#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
mkdir -p .state/realtime .state/qmd
LOG=".state/realtime/slack-realtime-$(date -u +%Y%m%dT%H%M%SZ).log"
LATEST_LOG=".state/realtime/slack-realtime.latest.log"
LOCK=".state/slack-realtime.lock"

run_inner() {
  echo "$(date -Is) realtime Slack update starting"
  if tmux has-session -t llm-wiki-slack-download-chunks 2>/dev/null; then
    BATCH_SIZE="${SLACK_REALTIME_BATCH_DURING_BACKFILL:-25}"
    echo "$(date -Is) full historical downloader is active; using conservative batch size ${BATCH_SIZE}"
  else
    BATCH_SIZE="${SLACK_REALTIME_BATCH_SIZE:-150}"
    echo "$(date -Is) full historical downloader not active; using batch size ${BATCH_SIZE}"
  fi
  python3 scripts/slack_incremental_chunks.py \
    --batch-size "$BATCH_SIZE" \
    --bootstrap-lookback-minutes "${SLACK_REALTIME_BOOTSTRAP_LOOKBACK_MINUTES:-1440}" \
    --overlap-minutes "${SLACK_REALTIME_OVERLAP_MINUTES:-15}" \
    --max-active-threads-per-run "${SLACK_REALTIME_ACTIVE_THREADS:-75}" \
    --max-history-pages-per-channel "${SLACK_REALTIME_MAX_HISTORY_PAGES:-5}" \
    --max-reply-pages-per-thread "${SLACK_REALTIME_MAX_REPLY_PAGES:-5}"

  RUN_ID="$(python3 - <<'PY'
import json, pathlib
p=pathlib.Path('.state/slack-realtime-last-run.json')
print(json.loads(p.read_text()).get('run_id','') if p.exists() else '')
PY
)"
  PAGES_WRITTEN="$(python3 - <<'PY'
import json, pathlib
p=pathlib.Path('.state/slack-realtime-last-run.json')
print(json.loads(p.read_text()).get('pages_written',0) if p.exists() else 0)
PY
)"
  echo "$(date -Is) realtime Slack update run_id=${RUN_ID} pages_written=${PAGES_WRITTEN}"
  if [[ -n "$RUN_ID" && "$PAGES_WRITTEN" != "0" ]]; then
    echo "$(date -Is) materializing ${RUN_ID} for QMD"
    python3 scripts/materialize_qmd_slack_chunks.py --run-id "$RUN_ID"
    echo "$(date -Is) qmd update"
    qmd update
  else
    echo "$(date -Is) no new Slack pages; skipping materialize/qmd update"
  fi
  echo "$(date -Is) realtime Slack update finished"
}

if ! flock -n "$LOCK" bash -c 'echo locked' >/dev/null 2>&1; then
  echo "$(date -Is) another realtime Slack update already holds ${LOCK}; exiting" | tee -a "$LOG"
  ln -sfn "$(basename "$LOG")" "$LATEST_LOG"
  exit 0
fi

# Hold the lock for the whole run in this process.
{
  flock -n 9 || { echo "$(date -Is) another realtime Slack update is running"; exit 0; }
  run_inner
} 9>"$LOCK" 2>&1 | tee -a "$LOG"
ln -sfn "$(basename "$LOG")" "$LATEST_LOG"
