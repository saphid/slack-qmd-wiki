---
title: Platform discussion — Azure Dev VPN and Splunk API access
type: source-summary
status: active
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1759377249-914259/page-000001.md
---

# Platform discussion — Azure Dev VPN and Splunk API access

This Slack thread clarifies how Azure Dev VPN behaves for outbound allow-listing and how Splunk API access might be routed.

Durable points:

- Azure Dev VPN does not route all internet-bound traffic through a static outbound IP; it mainly adds routes for Azure/private network traffic.
- Existing Testlord/Redshift access used a jumpbox/TCP proxy pattern that can provide a static outbound IP and DNS override while on VPN.
- Splunk API listens on port 8089 and could potentially be added to the same kind of jumpbox/proxy pattern.
- Temporarily allow-listing a home IP was used as a short-term hackathon workaround, not a durable pattern.

Links:

- [[pages/topics/azure-vpn-and-static-egress|Azure VPN and Static Egress]]
- [[pages/decisions/azure-dev-vpn-is-not-static-egress|Azure Dev VPN is not static egress]]

Source: `qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1759377249-914259/page-000001.md` ts=1759377249.914259 channel=platform-discussions
