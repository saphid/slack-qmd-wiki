---
title: Security review required for agent MCP login
type: decision
status: active
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1776036244-597439/page-000001.md
---

# Security review required for agent MCP login

## Decision

Agent MCP login using localhost callback plus LoginID/SessionID requires security review before production use. If the feature is only needed for local/test usage, gate it to debug or non-production environments until reviewed.

## Rationale

The Slack thread explicitly called the approach novel/risky because Displayr does not have OAuth for this path. Review concerns included token/session exposure to agent history or LLMs, later authentication semantics, effect on the user's browser session, and SOC 2 / penetration-test scrutiny.

Source: `qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1776036244-597439/page-000001.md` ts=1776047467.522609 channel=platform-discussions
