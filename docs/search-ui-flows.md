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
| Search options | `#mode`, `#limit`, `#sort`, collection checkboxes | Sends mode/limit/sort/collection to `/api/search`; unknown collections/modes return a 400 JSON error. | E2E sets mode/limit/sort before searching. |
| Filters | channel, user, date range, within-result text | Server-side post-filtering narrows returned results without shell interpolation. | E2E clears filters and verifies visible state resets. |
| Facet suggestions | `/api/facets`, main-query `user:`, `from:`, `in:`, `channel:` autocomplete, and capped `datalist` controls | Loads real local channel/user metadata when present; query autocomplete renders only the best matching suggestions so thousands of channel facets do not slow the page. Empty lists are valid in a generic checkout. | E2E verifies facet arrays, rejects `U-DEMO`/`C-DEMO`, and exercises user/channel query suggestions when facets exist. |
| Result rendering | `.result` cards | Shows title, QMD/local URI, corpus/date/channel badges, score, and snippet with highlighting. | E2E verifies at least one `.result`. |
| Result actions | Open source, copy URI/snippet/citation, JSON | Opens `/api/get` or local-file fallback, copies through browser clipboard when available, and shows raw JSON for debugging. | E2E exercises JSON, open, and copy URI. |
| Clear | `#clearButton`, `#clearFiltersButton` | Clears query/filter/result state visibly. | E2E verifies query and results are cleared. |

## Removed until justified

- Telemetry dashboard: operational status belongs in scripts/logs unless a small, tested status widget is needed.
- AI assist / reranking controls: QMD modes are enough for the core UI.
- Investigation boards, tabs, timeline, history, saved searches, share drawers, and settings rails: useful later only with explicit persistence semantics and test coverage.
