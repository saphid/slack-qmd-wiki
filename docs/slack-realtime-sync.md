# Slack realtime sync

The full historical Slack downloader writes durable API pages under
`chunks/slack/<run-id>`. Realtime freshness is handled separately by a
small incremental updater:

- `scripts/slack_incremental_chunks.py` keeps `.state/slack-realtime-sync-state.json`.
- `scripts/run_slack_realtime_update.sh` runs one locked update, materializes any
  new JSON pages to `qmd/slack-api-chunks/<run-id>`, then runs `qmd update`.
- `deploy/systemd/llm-wiki-slack-realtime.timer` runs the wrapper every five minutes once installed with `scripts/install_systemd_user_units.sh`.

Freshness model:

- Hot channels in `config/slack_realtime_channels.txt` are checked every run.
- All visible conversations are checked by round-robin batches.
- While the full historical backfill is still running, the wrapper uses the
  conservative `SLACK_REALTIME_BATCH_DURING_BACKFILL` batch size (default 25) so
  it does not starve the backfill on Slack rate limits.
- After the historical downloader exits, the batch size increases to
  `SLACK_REALTIME_BATCH_SIZE` (default 150), giving a complete active-conversation
  sweep in roughly a few hours rather than trying to poll thousands of channels
  every five minutes.
- Active threads observed in recent history are rechecked for new replies for
  seven days by default.

Operational commands:

```bash
scripts/install_systemd_user_units.sh
systemctl --user status llm-wiki-slack-realtime.timer
systemctl --user start llm-wiki-slack-realtime.service
journalctl --user -u llm-wiki-slack-realtime.service -n 100 --no-pager
tail -f .state/realtime/slack-realtime.latest.log
cat .state/slack-realtime-last-run.json
```

Tuning environment variables can be added to the user service if needed:

- `SLACK_REALTIME_BATCH_DURING_BACKFILL` (default `25`)
- `SLACK_REALTIME_BATCH_SIZE` (default `150`)
- `SLACK_REALTIME_BOOTSTRAP_LOOKBACK_MINUTES` (default `1440`)
- `SLACK_REALTIME_OVERLAP_MINUTES` (default `15`)
- `SLACK_REALTIME_ACTIVE_THREADS` (default `75`)

This is as close to realtime as is viable with Slack Web API polling and the
current OAuth grant. True push-based realtime would require a Slack app with the
Events API/socket mode and its own deployment/security review.
