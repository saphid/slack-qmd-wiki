---
title: Remaining Slack Corpus Ingestion Queue
type: question
status: open
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/
  - qmd/huddle-transcripts/
---

# Remaining Slack Corpus Ingestion Queue

This page is the explicit queue for continuing the Karpathy-style compile pass over all Slack messages, threads, and transcripts.

## Corpus still to process

- 66,183 Slack API markdown pages, including 58,879 reply/thread pages and 7,304 history pages.
- 200 huddle/standup transcript markdown pages.

## Recommended next batches

1. `team-devops` standups by month: source summaries plus topic pages for recurring operational work.
2. `platform-discussions` security/architecture threads: MCP login, SSO, Azure access, observability, deployment incidents.
3. High-signal `ask-devops`, `ask-security`, and `pagerduty` threads: operational runbooks and decision pages.
4. Service factory / baggage / company ID evidence across transcripts and Slack chunks.
5. Windows Kubernetes / SQL connectivity follow-up evidence.

## Done condition for a batch

Each batch should produce:

- A `wiki/sources/...` source summary.
- Updates to all affected topic/decision/question pages.
- `wiki/index.md` entries.
- A `wiki/log.md` append-only ingest entry.

Source: `qmd/slack-api-chunks/` corpus inventory, 66,183 files on 2026-05-03.
Source: `qmd/huddle-transcripts/` corpus inventory, 200 files on 2026-05-03.
