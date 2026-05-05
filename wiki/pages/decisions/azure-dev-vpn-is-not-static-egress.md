---
title: Azure Dev VPN is not static egress
type: decision
status: active
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1759377249-914259/page-000001.md
---

# Azure Dev VPN is not static egress

## Decision

Do not rely on Azure Dev VPN itself as a stable outbound IP for external allow-listing. Use a purpose-built proxy/jumpbox pattern or temporary direct allow-listing when appropriate.

## Rationale

The Slack thread explains that the VPN adds routes for Azure/private network traffic and does not route all internet-bound traffic through Azure. Existing Testlord/Redshift access used a proxy VM/DNS override pattern, which could be extended to Splunk API port 8089.

Source: `qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1759377249-914259/page-000001.md` ts=1759377707.612149 channel=platform-discussions
