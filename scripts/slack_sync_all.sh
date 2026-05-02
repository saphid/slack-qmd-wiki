#!/usr/bin/env bash
# Sync every Slack conversation the configured user OAuth token can access.
# This intentionally uses conversations.list rather than users.conversations so
# readable public channels that the user has not joined are included too.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
. .venv/bin/activate
export PYTHONUNBUFFERED=1
python scripts/slack_sync.py \
  --days "${SLACK_SYNC_DAYS:-10000}" \
  --include-dms \
  --include-archived \
  --conversations-list \
  "$@"
