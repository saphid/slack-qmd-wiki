---
title: Platform discussion — MCP login security review
type: source-summary
status: active
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1776036244-597439/page-000001.md
---

# Platform discussion — MCP login security review

This Slack thread started as a local SSO/SAML debugging question and turned into a security concern about an MCP tool for login from Claude Code.

Durable points:

- Local SSO/SAML debugging was considered rare and stale; ad-hoc environments were suggested as the easier verification path for SSO work.
- The proposed Claude Code login flow used a local HTTP callback, an MCP `get_login_url` tool, then `auth_token` and `session_id` returned to localhost after browser login.
- Reviewers flagged that using LoginID/SessionID this way is novel/risky, potentially visible to agent history/LLMs, and may interact badly with the one-active-session model.
- A security review ticket was requested before production use, with a suggestion to gate the feature to debug/non-production if not needed immediately.

Links:

- [[pages/topics/agent-mcp-login|Agent MCP Login]]
- [[pages/decisions/security-review-required-for-agent-mcp-login|Security review required for agent MCP login]]

Source: `qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1776036244-597439/page-000001.md` ts=1776036244.597439 channel=platform-discussions
