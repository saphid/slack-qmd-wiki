# QMD search tools

This workspace has two local search entry points over QMD indexes:

- CLI: `~/bin/llm-wiki-search` or `scripts/llm_wiki_search.py`
- Web UI: `http://127.0.0.1:8765` served by `scripts/qmd_search_server.mjs`

## CLI examples

```bash
llm-wiki-search "OAuth refresh token" -n 10
llm-wiki-search "deployment checklist" --collection raw -n 20
llm-wiki-search "cloud agent workstation" --collection wiki
llm-wiki-search "release planning action items" --collection meetings
llm-wiki-search "OAuth refresh token" --collection raw --json
llm-wiki-search "how did the Slack OAuth grant get set up" --mode hybrid --no-rerank
```

Collection aliases:

- `raw` / `slack` -> `slack-raw`
- `chunks` / `api-chunks` -> `slack-api-chunks`
- `conversation` / `conversations` / `batches` -> `slack-conversations` (channel/thread/conversation-oriented ingest chunks)
- `meeting` / `meetings` / `huddle` / `huddles` / `transcripts` -> `huddle-transcripts`
- `wiki` -> `llm-wiki`
- `all` -> all of the above

## Web UI

Start/restart the localhost web search service:

```bash
llm-wiki-search-web
# or
scripts/start_qmd_search_web_tmux.sh
```

The service binds to localhost by default:

```text
http://127.0.0.1:8765
```

Useful checks:

```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS 'http://127.0.0.1:8765/api/search?q=OAuth&mode=lex&collection=raw,wiki&n=25&sort=date-desc'
curl -fsS 'http://127.0.0.1:8765/api/search?q=platform&mode=lex&collection=chunks,wiki&relative=last-year'
```

The web API default maximum is 500 results per search (`LLM_WIKI_SEARCH_MAX_RESULTS` can override it). This is a process guardrail, not a QMD limit.

## Core web features

The web UI intentionally keeps only the reliable search path:

- polished dark command/search bar with optional decorators: `user:`, `from:`, `in:`, `channel:`, `after:`, `before:`, `on:`, `corpus:`, `mode:`, `sort:`
- desktop two-column results/preview layout with source and filter controls in the top search area
- search mode: lexical, hybrid, or vector
- source selection: Slack messages and Wiki checked by default; Conversations, Transcripts, and Raw Slack are opt-in
- result limit and server-side sort
- top-bar channel, user, relative time (`last week`, `last month`, `last year`), absolute after/before date, and within-result filters with active chips near the search bar
- real local user/channel datalist suggestions from `/api/facets` when metadata exists, plus resolved query-criteria chips for known `user:`, `from:`, `in:`, and `channel:` decorators
- result cards with corpus/date/channel metadata, score and match badges, highlighted snippets, selected state, known Slack user/channel chips in place of raw mention/channel tokens, and a compact matches summary with clickable user/channel count chips
- click-to-preview source lines in the right panel via `/api/get`, plus open source lines, copy URI/snippet/citation, and view JSON actions
- graceful local-markdown fallback when QMD is unavailable or a generic checkout has no configured QMD collections

The server calls `qmd` with argument arrays and does not shell-interpolate user input. It should remain localhost-only unless there is an explicit review to expose it.

## Removed from the web UI

The prior page included telemetry dashboards, an AI assist button, investigation boards, saved/history/settings rails, tabs, timelines, source weighting sliders, and other half-wired controls. They were removed from the core UI because they made reliability hard to reason about and were not necessary for local search.

If one of those capabilities becomes necessary, add it back as a small feature with real state, documentation in `docs/search-ui-flows.md`, and browser E2E coverage.

## Proof commands

```bash
./scripts/check.sh
node --check scripts/qmd_search_server.mjs
python3 scripts/e2e_qmd_search.py
```

For a generic/public checkout without QMD collections, run the web server with a test port and rely on the labelled local markdown fallback:

```bash
LLM_WIKI_SEARCH_PORT=8768 node scripts/qmd_search_server.mjs
QMD_SEARCH_BASE_URL=http://127.0.0.1:8768 python3 scripts/e2e_qmd_search.py
```

## Running services

- Web UI tmux session: `llm-wiki-qmd-search-web`
- Web UI log: `.state/qmd/qmd-search-web.latest.log`
- QMD embedding session: `llm-wiki-qmd-embed`
- Final refresh watcher: `llm-wiki-qmd-final-refresh-watcher`

The final refresh watcher materializes downloaded Slack API JSON chunks into markdown under `qmd/slack-api-chunks/<run-id>/`, materializes sanitized huddle/standup transcript text files under `qmd/huddle-transcripts/` when local transcript roots exist, updates QMD, embeds, and restarts the QMD HTTP daemon after the Slack download finishes. When `LLM_WIKI_INDEX_CONVERSATIONS=true`, it also materializes conversation-oriented ingest chunks under `qmd/slack-conversations/<run-id>/`.

For `qmd/slack-conversations`, Slack threads and transcripts are deterministic one-chunk cases. Unthreaded channel timelines are the inferred case: set `SLACK_CONVERSATION_CHANNEL_MODE=llm-with-heuristic-fallback`, `SLACK_CONVERSATION_LLM_PROVIDER=ollama`, `SLACK_CONVERSATION_LLM_MODEL=qwen2.5:7b-instruct`, and `SLACK_CONVERSATION_WORKERS=<n>` to have multiple cheap local LLM workers segment different channels in parallel. To use Pi coding agent instead, set `SLACK_CONVERSATION_LLM_PROVIDER=pi` and optionally `SLACK_CONVERSATION_PI_MODEL=<pi-model>`. Use `SLACK_CONVERSATION_CHANNEL_MODE=llm` for strict no-fallback runs.

Transcript search UI: the `huddle-transcripts` QMD collection is exposed as a Transcripts source, with transcript-specific badges, metadata, and speaker/time preview rendering when source text follows `Speaker [m:ss]: ...` lines.
