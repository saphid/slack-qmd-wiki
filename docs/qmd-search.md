# QMD search tools

This workspace has two local search entry points over QMD indexes:

- CLI: `~/bin/llm-wiki-search` or `scripts/llm_wiki_search.py`
- Web UI: `http://127.0.0.1:8765` served by `scripts/qmd_search_server.mjs`

## CLI examples

```bash
llm-wiki-search "OAuth refresh token" -n 10
llm-wiki-search "deployment checklist" --collection raw -n 20
llm-wiki-search "cloud agent workstation" --collection wiki
llm-wiki-search "OAuth refresh token" --collection raw --json
llm-wiki-search "how did the Slack OAuth grant get set up" --mode hybrid --no-rerank
```

Collection aliases:

- `raw` / `slack` -> `slack-raw`
- `chunks` / `api-chunks` -> `slack-api-chunks`
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
```

The web API default maximum is 500 results per search (`LLM_WIKI_SEARCH_MAX_RESULTS` can override it). This is a process guardrail, not a QMD limit.

## Core web features

The web UI intentionally keeps only the reliable search path:

- query box with optional decorators: `user:`, `from:`, `in:`, `channel:`, `after:`, `before:`, `on:`, `corpus:`, `mode:`, `sort:`
- search mode: lexical, hybrid, or vector
- collection selection: Slack API chunks, wiki, raw Slack, or any combination
- result limit and server-side sort
- optional channel, user, date, and within-result filters
- real local user/channel datalist suggestions from `/api/facets` when metadata exists
- result cards with corpus/date/channel metadata, highlighted snippets, and source URI
- result actions: open source lines, copy URI/snippet/citation, and view JSON
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

The final refresh watcher materializes downloaded Slack API JSON chunks into markdown under `qmd/slack-api-chunks/<run-id>/`, updates QMD, embeds, and restarts the QMD HTTP daemon after the Slack download finishes.
