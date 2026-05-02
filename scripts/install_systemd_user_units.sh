#!/usr/bin/env bash
set -euo pipefail
ROOT="${LLM_WIKI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"

# The shipped unit uses %h/slack-qmd-wiki by default. If this clone lives
# somewhere else, write a local override with LLM_WIKI_ROOT and the actual
# ExecStart path.
install -m 0644 "$ROOT/deploy/systemd/llm-wiki-slack-realtime.timer" "$UNIT_DIR/llm-wiki-slack-realtime.timer"
if [[ "$ROOT" == "$HOME/slack-qmd-wiki" ]]; then
  install -m 0644 "$ROOT/deploy/systemd/llm-wiki-slack-realtime.service" "$UNIT_DIR/llm-wiki-slack-realtime.service"
else
  cat > "$UNIT_DIR/llm-wiki-slack-realtime.service" <<UNIT
[Unit]
Description=LLM wiki Slack near-real-time incremental chunk sync
Documentation=file:$ROOT/docs/slack-realtime-sync.md

[Service]
Type=oneshot
WorkingDirectory=$ROOT
Environment=PATH=$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:/bin
Environment=LLM_WIKI_ROOT=$ROOT
ExecStart=$ROOT/scripts/run_slack_realtime_update.sh
UNIT
fi
systemctl --user daemon-reload
systemctl --user enable --now llm-wiki-slack-realtime.timer
systemctl --user status llm-wiki-slack-realtime.timer --no-pager
