---
title: Karpathy LLM Wiki Operating Model
type: topic
status: active
updated: 2026-05-03
sources:
  - https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
---

# Karpathy LLM Wiki Operating Model

This wiki follows Andrej Karpathy's LLM Wiki pattern: raw sources remain immutable evidence; the LLM-maintained `wiki/` directory is the compiled, persistent knowledge layer; `wiki/index.md` is the content map; and `wiki/log.md` is append-only operational history.

For Displayr Slack, the raw evidence layers are:

- `qmd/slack-api-chunks/` — searchable markdown materialized from Slack API history and thread/reply pages.
- `qmd/huddle-transcripts/` — searchable markdown materialized from sanitized Slack huddle/standup transcripts.
- `raw/slack/` — older raw Slack markdown exports.

The wiki should not claim that QMD search results are the durable wiki. Search is how maintainers find evidence; ingest is the step that compiles durable facts into source summaries, topic pages, decision pages, question pages, links, and citations.

Source: `https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f`
