---
title: Slack Corpus Processing
type: topic
status: active
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/
  - qmd/huddle-transcripts/
---

# Slack Corpus Processing

The current Slack evidence corpus is large enough that wiki maintenance must be incremental and queue-driven rather than a single exhaustive read.

Current indexed source shape on the VM at the first ingest pass:

- `qmd/slack-api-chunks/`: 66,183 markdown files.
  - History pages: 7,304.
  - Reply/thread pages: 58,879.
- `qmd/huddle-transcripts/`: 200 markdown transcript files.

The intended processing loop is:

1. Keep materializing raw Slack API data and transcript `.txt` files into QMD markdown.
2. Use QMD lexical/vector search and file inventories to select bounded evidence batches.
3. For each batch, write a source summary page under `wiki/sources/...`.
4. Update durable pages under `wiki/pages/topics/`, `wiki/pages/decisions/`, and `wiki/pages/questions/`.
5. Update `wiki/index.md` and append `wiki/log.md`.
6. Keep an explicit remaining queue instead of pretending the entire corpus has been semantically digested.

The first pass intentionally compiled high-signal DevOps/platform topics from both transcript evidence and Slack thread evidence. Subsequent passes should continue by channel/topic and mark processed source files in ingest manifests.

Source: `qmd/slack-api-chunks/` corpus inventory, 66,183 files on 2026-05-03.
Source: `qmd/huddle-transcripts/` corpus inventory, 200 files on 2026-05-03.
