#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT"
WATCH_SESSION="${WATCH_SESSION:-llm-wiki-slack-download-chunks}"
printf "watch_started_at=%s session=%s\n" "$(date -Is)" "$WATCH_SESSION"
while tmux has-session -t "$WATCH_SESSION" 2>/dev/null; do
  sleep 300
done
printf "download_session_finished_at=%s\n" "$(date -Is)"
./scripts/backup_slack_chunks_to_blob.sh
printf "final_backup_finished_at=%s\n" "$(date -Is)"
