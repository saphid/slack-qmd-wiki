# QMD Search UI flows

This page is intentionally a single-page local web UI. Visible controls must either perform a visible action or be hidden.

## Data policy

- Autosuggest is backed only by `/api/facets` from local Slack/QMD metadata.
- The client must never fabricate people, channels, or Slack examples.
- If no facet metadata is present, decorator autosuggest shows an honest empty state.
- The public repo must not commit Slack data, QMD indexes, chunks, raw exports, `.state`, `.env`, or tokens.

## Primary flows

| Flow | Controls | Expected behavior | Proof selector/API |
| --- | --- | --- | --- |
| Search | `#query`, `.search-submit` | Calls `/api/search`, renders result cards, records local history. If `qmd` is missing, local markdown fallback is clearly labelled. | `#status`, `.result` |
| Decorator autosuggest | `user:`, `from:`, `in:`, `channel:` in `#query` | Shows real facet suggestions when available; otherwise an empty-state message. | `#queryAutocomplete` |
| Query chips | `#queryTokens [data-remove-token]`, `#addFilterChip` | Remove parsed decorators or focus query for manual filter entry. | `#query` value |
| Modes | `[data-mode]` | Updates active mode and hidden `#mode`; next search sends that mode. | `#mode` |
| Result options | `#limit`, `#sort`, `#rerank` | Limit/sort/rerank are reflected in search params; sort also reorders current returned set. | `/api/search` params, `#status` |
| Sources | `input[name=collection]`, `#slackWeight`, `#obsidianWeight` | Collections are sent to search; weights affect relevance ordering where backend has mixed sources. | `/api/search` params |
| Slack filters | `#channel`, `#includeDms`, `#user`, date range controls | Sent to `/api/search` and applied server-side. | `#status`, returned counts |
| Wiki filters | `#tagFilter`, `#folderFilter` | Populated from real wiki facets and sent to `/api/search`; empty when unavailable. | `/api/facets`, `#tagChips` |
| Returned-result facets | facet strip and facet chips | Narrow only the current returned result set without re-querying. | `#resultFacets`, visible result count |
| Tabs/views/timeline | result tabs, `[data-view]`, `#timelineView` | Tabs filter current results; grid/list changes layout; timeline sorts current results newest first. | `#results.className`, `#status` |
| Result actions | Open lines, Copy URI, Copy snippet, JSON, raw snippet | Open source/JSON dialogs or copy text; raw snippet expands inline. | `#docDialog`, clipboard/status |
| Saved searches | `#saveSearch`, Saved rail | Save named local search; Saved opens a modal list with Run/Copy/Delete. | localStorage, dialog buttons |
| History | history icon, History rail | Opens modal list with Run/Copy/Delete. No browser alert. | localStorage, dialog buttons |
| Share | `#shareSearch` | Copies current URL plus query params and reports status. | `#status` |
| Help/more | `#docsButton`, `#moreMenu`, Settings rail | Opens actionable modal content. | `#docDialog` |
| Clear | `#clearAllTop`, `#clearFilters`, `#clearQuery` | Clears the intended scope visibly; Clear all removes results too. | `#query`, `#results`, `#status` |
| Telemetry | `#telemetry`, `#refreshTelemetry` | Loads `/api/telemetry`; handles missing qmd gracefully. | `#telemetryBody` |

## Hidden until implemented

Boards, alerts, AI natural-language rewriting, and bottom investigation board controls are not visible in the generic public UI. They should only be reintroduced with real state, persistence, and E2E coverage.
