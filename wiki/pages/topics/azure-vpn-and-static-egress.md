---
title: Azure VPN and Static Egress
type: topic
status: active
updated: 2026-05-03
sources:
  - qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1759377249-914259/page-000001.md
---

# Azure VPN and Static Egress

Azure Dev VPN is useful for access to private Azure resources, but it should not be treated as a generic static outbound IP for external allow-listing.

First-pass synthesis:

- The VPN client routes private/Azure-bound traffic; normal internet-bound requests do not necessarily exit via Azure.
- Therefore the VPN itself does not provide a stable egress IP for external systems like Splunk Cloud.
- Existing patterns for Testlord and Redshift use a VM/jumpbox TCP proxy and DNS override to provide stable routed access.
- Splunk API on port 8089 could likely use a similar proxy/jumpbox pattern if needed.

Decision:

- [[pages/decisions/azure-dev-vpn-is-not-static-egress|Azure Dev VPN is not static egress]]

Source: `qmd/slack-api-chunks/all-feeds-20260502/C049F5TQB08/replies/1759377249-914259/page-000001.md` ts=1759377249.914259 channel=platform-discussions
