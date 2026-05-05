# Displayr LLM Wiki

A private, local-first Slack-to-markdown knowledge pipeline. It downloads Slack messages your token can already access, stores raw API pages locally, and indexes materialized markdown with QMD so an LLM agent can search and maintain a curated wiki.

This repository contains **code, docs, and examples only**. It intentionally does not contain Slack exports, API chunks, QMD indexes, `.env`, OAuth tokens, or runtime state.

## What this gives you

- Full-history Slack API chunk downloading with resumable state.
- Near-real-time incremental polling every five minutes via a systemd user timer.
- QMD materialization/indexing for lexical search over Slack chunks and wiki pages.
- Optional localhost web search UI over QMD results.
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
scripts/llm_wiki_search.py "qserver deploy" --collection chunks --json
scripts/llm_wiki_search.py "qserver deploy" --collection conversations --json
scripts/llm_wiki_search.py "release planning action items" --collection meetings
```

Web UI:

```bash
scripts/start_qmd_search_web_tmux.sh
curl -fsS http://127.0.0.1:8765/health
```

See `docs/qmd-search.md` for UI features and query decorators.

## Huddle and standup transcripts

Transcript fetching is intentionally outside this repo. Use the existing Work
skill/script to write sanitized `.txt` transcript folders under a local docs
root such as `~/Work/Docs/huddle-transcripts-*` or
`~/Work/Docs/standup-transcripts-*`. If QMD runs on the VM, copy those sanitized
folders there first, for example:

```bash
rsync -av ~/Work/Docs/huddle-transcripts-* alex@vm:/home/alex/Work/Docs/
rsync -av ~/Work/Docs/standup-transcripts-* alex@vm:/home/alex/Work/Docs/
```

Materialize and index the transcripts with the normal refresh path. Set `LLM_WIKI_INDEX_CONVERSATIONS=true` when you also want conversation-oriented Slack ingest chunks under `qmd/slack-conversations/`: Slack threads and transcripts are deterministic one-chunk cases; unthreaded channel messages can be grouped by a cheap/fast LLM into detected in-channel conversations.

By default this falls back to deterministic heuristics so unattended refresh jobs do not hang on a missing LLM. For LLM channel segmentation, run with a local Ollama model or command-backed cheap model, and fan out by channel with workers:

```bash
# Local/private cheap option; pull the model once outside the script.
ollama pull qwen2.5:7b-instruct
SLACK_CONVERSATION_CHANNEL_MODE=llm-with-heuristic-fallback \
SLACK_CONVERSATION_LLM_PROVIDER=ollama \
SLACK_CONVERSATION_LLM_MODEL=qwen2.5:7b-instruct \
SLACK_CONVERSATION_WORKERS=4 \
python3 scripts/materialize_qmd_conversation_chunks.py --run-id "$SLACK_RUN_ID"
```

Or use Pi coding agent as the segmenter:

```bash
SLACK_CONVERSATION_CHANNEL_MODE=llm-with-heuristic-fallback \
SLACK_CONVERSATION_LLM_PROVIDER=pi \
SLACK_CONVERSATION_PI_MODEL=gemini-3-flash-preview \
SLACK_CONVERSATION_WORKERS=3 \
python3 scripts/materialize_qmd_conversation_chunks.py --run-id "$SLACK_RUN_ID"
```

Use `SLACK_CONVERSATION_CHANNEL_MODE=llm` when you want strict failure instead of heuristic fallback for any failed LLM window.

```bash
LLM_WIKI_INDEX_CONVERSATIONS=true scripts/refresh_qmd_hybrid_indexes.sh
```

For a narrow local run:

```bash
python3 scripts/materialize_qmd_huddle_transcripts.py --source-root ~/Work/Docs
qmd collection add qmd/huddle-transcripts --name huddle-transcripts
qmd update
qmd embed --max-docs-per-batch "${QMD_MAX_DOCS_PER_BATCH:-200}" --max-batch-mb "${QMD_MAX_BATCH_MB:-64}"
```

Then query them lexically or semantically if the selected QMD embedding/model
setup is available:

```bash
scripts/llm_wiki_search.py "decision action item" --collection meetings
scripts/llm_wiki_search.py "why did we choose the rollout path" --collection meetings --mode hybrid --no-rerank
```

To hand new transcript evidence to the LLM-maintained wiki process, create an
inbox manifest:

```bash
python3 scripts/create_huddle_transcript_wiki_manifest.py
```

The wiki maintainer should summarize durable meeting outcomes, decisions, risks,
and action items into `wiki/` pages with compact citations to the materialized
transcript path and `source_path`; it should not copy whole transcripts into the
wiki.

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

The check compiles Python scripts, optionally runs ShellCheck if installed, and validates the Node search server syntax if Node is installed.
