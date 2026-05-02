# Slack QMD Wiki

A generic, local-first Slack-to-markdown knowledge pipeline. It downloads Slack messages your token can already access, stores raw API pages locally, and indexes materialized markdown with [QMD](https://www.npmjs.com/package/@tobilu/qmd) so an LLM agent can search and maintain a curated wiki.

This repository contains **code, docs, and examples only**. It intentionally does not contain Slack exports, API chunks, QMD indexes, `.env`, OAuth tokens, or runtime state.

## What this gives you

- Full-history Slack API chunk downloading with resumable state.
- Near-real-time incremental polling every five minutes via a systemd user timer.
- QMD materialization/indexing for lexical search over Slack chunks and wiki pages.
- Optional localhost web search UI over QMD results.
- Telemetry dashboard for Slack download progress, QMD index counts, realtime updates, and wiki size.
- A persistent wiki workflow (`AGENTS.md`) that keeps source evidence separate from synthesized knowledge.

## Data and secret safety

Generated/local-only paths are gitignored:

- `.env`, `.env.*`, `.venv/`, `.state/`
- `raw/slack/`, `chunks/`, `qmd/`, `inbox/`
- local channel filters under `config/slack_channels.txt` and `config/slack_realtime_channels.txt`

Commit only example configs. Never commit raw Slack data, chunk JSON, QMD markdown/indexes, OAuth tokens, or backup manifests.

## Slack access model

Slack API access is governed by the OAuth token you provide:

- A **user token** is the closest match for "channels I can access".
- A **bot token** usually only sees channels the bot has been added to.
- Private channel history requires the token/app to have access to those private channels.
- This setup does not bypass Slack permissions.

Recommended OAuth scopes for a user-token sync:

```text
users:read
channels:read channels:history
groups:read groups:history
mpim:read mpim:history
im:read im:history      # only if you explicitly include DMs
links:read              # optional, useful for richer messages
```

## Setup

```bash
git clone https://github.com/<owner>/slack-qmd-wiki.git
cd slack-qmd-wiki
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set SLACK_USER_TOKEN or SLACK_BOT_TOKEN
cp config/slack_channels.example.txt config/slack_channels.txt
cp config/slack_realtime_channels.example.txt config/slack_realtime_channels.txt
```

The sync scripts read `.env` automatically. You can also export tokens in your shell.

Install QMD if you want search/indexing:

```bash
npm install -g @tobilu/qmd
```

## Historical backfill

Download Slack API pages into durable chunk files:

```bash
python scripts/slack_download_chunks.py \
  --days 10000 \
  --include-archived \
  --types public_channel,private_channel,mpim,im \
  --run-id all-feeds
```

Materialize completed chunks and update QMD lexical search:

```bash
SLACK_RUN_ID=all-feeds scripts/incremental_qmd_slack_chunk_index.sh
```

## Near-real-time updates

The incremental updater polls Slack, writes only newly observed pages under `chunks/slack/realtime-*`, materializes those pages, and runs `qmd update`.

One-shot run:

```bash
scripts/run_slack_realtime_update.sh
```

Install the five-minute user timer:

```bash
scripts/install_systemd_user_units.sh
```

Check it:

```bash
systemctl --user status llm-wiki-slack-realtime.timer
journalctl --user -u llm-wiki-slack-realtime.service -n 100 --no-pager
tail -f .state/realtime/slack-realtime.latest.log
```

See `docs/slack-realtime-sync.md` for the freshness model and tuning knobs.

## QMD search

CLI:

```bash
scripts/llm_wiki_search.py "OAuth refresh token" -n 10
scripts/llm_wiki_search.py "deployment checklist" --collection chunks --json
```

Web UI:

```bash
scripts/start_qmd_search_web_tmux.sh
curl -fsS http://127.0.0.1:8765/health
```

See `docs/qmd-search.md` for UI features and query decorators.

## Wiki workflow

`AGENTS.md` defines the LLM-maintained wiki contract:

- raw sources are evidence, not the wiki
- `wiki/` is the curated synthesized layer
- claims from Slack need citations to source paths/permalinks
- sensitive Slack content should be summarized minimally

## Validation

```bash
scripts/check.sh
```

The check compiles Python scripts, runs ShellCheck if installed, and validates the Node search server syntax if Node is installed.
