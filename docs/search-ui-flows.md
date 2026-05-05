# QMD Search UI flows

The web UI is deliberately small. Visible controls must prove one of the core search flows below; speculative rails, AI rewriting, boards, telemetry dashboards, saved-search drawers, and fake demo data stay out until they have real state and E2E coverage.

## Data policy

- Suggestions come only from `/api/facets` and local Slack/QMD metadata.
- The client must never fabricate people, channels, Slack examples, or results.
- Public code must not commit Slack data, QMD indexes, chunks, raw exports, `.state`, `.env`, or tokens.

## Core flows

| Flow | Controls/API | Expected behavior | Proof |
| --- | --- | --- | --- |
| Service health | `#healthButton`, `/health` | Reports localhost service health and configured max result cap. | E2E clicks health and sees `Service OK`. |
| Search | `#query`, submit button, `/api/search` | Runs QMD with argument arrays; if QMD is unavailable or unusable in a generic checkout, falls back to local markdown and labels the fallback. | API smoke + browser E2E return at least one result for `wiki`. |
| Search options | `#mode`, `#limit`, `#sort`, source checkboxes | Sends mode/limit/sort/collection to `/api/search`; unknown collections/modes return a 400 JSON error. | E2E sets mode/limit/sort before searching. |
| Filters | top-bar source checkboxes, channel, user, relative time, after/before date pickers, and within-result text | Server-side post-filtering narrows returned results without shell interpolation. Active chips mirror current filters near the search bar. Relative options (`last week`, `last month`, `last year`) compute a server-side lower date bound unless an explicit `after` date is set. | E2E exercises last-year filtering, clears filters, and verifies visible state resets. |
| Facet suggestions | `/api/facets`, main-query `user:`, `from:`, `in:`, `channel:` autocomplete, capped `datalist` controls, and resolved search-criteria chips | Loads real local channel/user metadata when present; query autocomplete renders only the best matching suggestions so thousands of channel facets do not slow the page. Known query decorators render as removable user/channel chips when they resolve to real facets. Empty lists are valid in a generic checkout. | E2E verifies facet arrays, rejects `U-DEMO`/`C-DEMO`, and exercises user/channel query suggestions and known channel criteria chips when facets exist. |
| Result rendering | `.result` cards and `#resultTabs` summary chips | Shows title, QMD/local URI, corpus/date/channel badges, score, match badges, selected state, highlighted snippets, known Slack user/channel entity chips instead of raw mention/channel tokens, total found, and current-result channel/user chips with counts. Clicking a result-summary chip copies it into the top filters and reruns the search. | E2E verifies at least one `.result`, entity-chip rendering/click filtering, summary text, optional summary-chip narrowing, and the selected preview state. |
| Preview | right-side preview panel | Clicking a result fetches source lines via `/api/get`, shows the selected card state, resolves known Slack entities in message text, and explains why it matched using existing score/filter/term metadata. | E2E clicks a result and waits for the preview panel source. |
| Result actions | Open source, copy URI/snippet/citation, JSON | Open source opens the source dialog using `/api/get`, copies through browser clipboard when available, and shows raw JSON for debugging. | E2E exercises JSON, open, and copy URI. |
| Clear | `#clearButton`, `#clearFiltersButton` | Clears query/filter/result state visibly. | E2E verifies query and results are cleared. |

## Removed until justified

- Telemetry dashboard: operational status belongs in scripts/logs unless a small, tested status widget is needed.
- AI assist / reranking controls: QMD modes are enough for the core UI.
- Investigation boards, tabs, timeline, history, saved searches, share drawers, and settings rails: useful later only with explicit persistence semantics and test coverage.


Transcript search UI: the `huddle-transcripts` QMD collection is exposed as a Transcripts source, with transcript-specific badges, metadata, and speaker/time preview rendering when source text follows `Speaker [m:ss]: ...` lines.
