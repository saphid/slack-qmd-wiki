# QMD search tools

This workspace has two local search entry points over QMD indexes:

- CLI: `~/bin/llm-wiki-search` or `scripts/llm_wiki_search.py`
- Web UI: `http://127.0.0.1:8765` served by `scripts/qmd_search_server.mjs`

## CLI examples

```bash
# Fast lexical/BM25 search across all configured corpora
llm-wiki-search "OAuth refresh token" -n 10

# Search only raw Slack markdown
llm-wiki-search "deployment checklist" --collection raw -n 20

# Search only generated wiki pages
llm-wiki-search "cloud agent workstation" --collection wiki

# JSON output for scripting
llm-wiki-search "OAuth refresh token" --collection raw --json

# Hybrid QMD search; slower on this CPU-only VM
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

The web API default maximum is 500 results per search (`LLM_WIKI_SEARCH_MAX_RESULTS` can override it). This is a UI/process guardrail, not a QMD limit.

Useful checks:

```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS 'http://127.0.0.1:8765/api/search?q=OAuth&mode=lex&collection=raw,wiki&n=75&sort=date-desc'
```

The web server does not shell-interpolate user input; it calls `qmd` with argument arrays and only allows the known collection aliases above. It should remain localhost-only unless there is an explicit reason to expose it.

## Running services

- Web UI tmux session: `llm-wiki-qmd-search-web`
- Web UI log: `.state/qmd/qmd-search-web.latest.log`
- QMD embedding session: `llm-wiki-qmd-embed`
- Final refresh watcher: `llm-wiki-qmd-final-refresh-watcher`

The final refresh watcher materializes downloaded Slack API JSON chunks into markdown under `qmd/slack-api-chunks/<run-id>/`, updates QMD, embeds, and restarts the QMD HTTP daemon after the Slack download finishes.


## Web UI features

The web UI is optimized for Slack/wiki investigation workflows:

- choose corpus: Slack API chunks, raw Slack markdown, wiki, or combinations
- choose search mode: lexical/BM25, hybrid QMD, or vector
- request up to 500 results per search
- sort by relevance, newest, oldest, channel/path, or corpus
- post-filter by channel/path, user/speaker, date range, and text within returned results
- channel and user dropdowns are populated from the Slack export/chunk metadata; users show Slack display name plus full/real name when Slack provides it
- the main search box validates `from:`, `user:`, `in:`, and `channel:` decorators against known Slack users/channels
- typing a decorator prefix opens autocomplete suggestions; `Tab`, `Enter`, or click inserts the canonical user/channel value
- searches with unknown user/channel decorators are blocked until corrected
- exact user filters extract matching Slack message blocks so `user:jordan` does not show `Jordana`, `Jordan Example`, etc.
- search decorators in the main search box:
  - `from:"Jordan Example"` or `user:Jamie`
  - `in:#general` or `channel:random`
  - `after:2026-01-01`, `before:2026-02-01`, or `on:2026-02-26`
  - `corpus:raw`, `corpus:chunks`, `corpus:wiki`
  - `mode:hybrid`, `mode:lex`, `mode:vec`
  - `sort:newest`, `sort:oldest`, `sort:channel`, `sort:corpus`
- inspect result metadata: corpus, date, channel/path, score, qmd URI
- content-aware result formatting: Slack results are rendered as Slack-like message/thread blocks; wiki and other documents are rendered as document snippets
- compact result-screen facet toolbar computed from the returned result set: corpus, channel/path, date, and user/speaker dropdowns with counts; clicking a facet filters the already-returned results instantly without another QMD query
- raw QMD snippets remain available in a collapsible section for debugging/copying
- open the first lines of a result with `qmd get`
- copy qmd URI, snippet, or a markdown citation
- view the raw JSON result for debugging or automation

Date/channel/user filters are post-retrieval filters because QMD does not currently expose Slack-specific facets. User decorators are also added to lexical queries to improve retrieval. If filters look too sparse, increase the requested result count.
