---
title: Agent MCP Login
type: topic
status: active
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1776036244-597439/page-000001.md
---

# Agent MCP Login

Agent MCP login refers to a proposed Claude Code / MCP flow where an agent starts a local HTTP listener, requests a login URL, and receives login/session material back at localhost after a browser login.

Known flow from Slack evidence:

1. Start a local HTTP server/listener.
2. Call an MCP tool to get a login URL with a localhost callback.
3. Browser login redirects to localhost with `auth_token` and `session_id`.
4. The agent/tooling uses that session material for subsequent authenticated actions.

Security concerns:

- The flow uses LoginID/SessionID in a novel way because Displayr does not have OAuth for this path.
- Session values may appear in agent history or be sent to LLMs unless carefully protected.
- The one-active-session model may log users out of browser sessions.
- A security review was requested before production use.

Decision:

- [[pages/decisions/security-review-required-for-agent-mcp-login|Security review required for agent MCP login]]

Source: `qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1776036244-597439/page-000001.md` ts=1776046210.289219 channel=platform-discussions
