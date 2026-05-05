# Wiki Index

This is the content-oriented map of the Slack-fed wiki. Read this first before answering questions or ingesting new sources.

## Start here

- [[log|Log]] — chronological operations log.
- [[pages/topics/karpathy-llm-wiki-operating-model|Karpathy LLM Wiki Operating Model]] — how raw Slack evidence is compiled into this persistent wiki.
- [[pages/topics/slack-corpus-processing|Slack Corpus Processing]] — corpus scale, ingestion loop, and queue discipline.

## Channels

- `platform-discussions` — first source summaries cover MCP login security and Azure VPN/Splunk access.
- `team-devops` — first source summaries cover standups for observability, Windows SQL connectivity, and service/platform status.

## Topics

- [[pages/topics/service-factory-rollout|Service Factory Rollout]] — rollout status, bug-squashing, review pressure, and promotion pipeline context.
- [[pages/topics/guardrails-team-swap|Guardrails Team Swap]] — controlled rollout plan for Guardrails permissions/team swap.
- [[pages/topics/observability-company-id-and-splunk|Observability, Company ID, and Splunk]] — company-context propagation, Splunk logs/traces, and Grafana fallback.
- [[pages/topics/windows-kubernetes-sql-connectivity|Windows Kubernetes SQL Connectivity]] — intermittent SQL transport failures on Windows Kubernetes nodes.
- [[pages/topics/agent-mcp-login|Agent MCP Login]] — local callback/auth-token/session-id login flow for Claude Code/MCP.
- [[pages/topics/azure-vpn-and-static-egress|Azure VPN and Static Egress]] — VPN routing, proxy/jumpbox static egress, Splunk API access.

## Decisions

- [[pages/decisions/prefer-grafana-when-splunk-trace-ingest-is-unreliable|Prefer Grafana when Splunk trace ingest is unreliable]]
- [[pages/decisions/security-review-required-for-agent-mcp-login|Security review required for agent MCP login]]
- [[pages/decisions/azure-dev-vpn-is-not-static-egress|Azure Dev VPN is not static egress]]

## Open questions

- [[pages/questions/windows-sql-duplicate-name-root-cause|Windows SQL duplicate-name root cause]]
- [[pages/questions/remaining-slack-corpus-ingestion-queue|Remaining Slack Corpus Ingestion Queue]]

## Source summaries

### Huddle transcripts

- [[sources/huddle-transcripts/2026-04-20-mark-service-factor-guardrails|2026-04-20 Mark Reid huddle — service factory and guardrails]]
- [[sources/huddle-transcripts/2026-04-13-devops-standup-observability|2026-04-13 DevOps standup — traces, baggage, and Splunk]]
- [[sources/huddle-transcripts/2025-11-28-devops-standup-windows-sql|2025-11-28 DevOps standup — Windows SQL connection investigation]]

### Slack API chunks

- [[sources/slack-api-chunks/platform-discussions-mcp-login-security-2026-04-13|Platform discussion — MCP login security review]]
- [[sources/slack-api-chunks/platform-discussions-azure-vpn-splunk-2025-10-02|Platform discussion — Azure Dev VPN and Splunk API access]]
