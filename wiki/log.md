# Wiki Log

Append-only chronological record. Entries should start with `## [YYYY-MM-DD] <operation> | <summary>`.

## [2026-05-02] setup | Created Slack-fed LLM wiki scaffold

Created the raw/source/wiki layout, Slack sync tooling contract, and initial index/log files.

## [2026-05-03] ingest | Initial Slack corpus compile pass

Compiled the first durable Karpathy-style wiki pass from both Slack huddle/standup transcripts and Slack API message/thread chunks.

Source scope:

- Transcript evidence from `qmd/huddle-transcripts/`, especially Mark Reid huddle and DevOps standups.
- Slack message/thread evidence from `qmd/slack-api-chunks/`, especially `platform-discussions` threads for MCP login security and Azure VPN/Splunk API access.
- Corpus inventory: 66,183 Slack API markdown pages, 58,879 reply/thread pages, 7,304 history pages, and 200 transcript markdown pages.

Touched pages:

- `wiki/pages/topics/karpathy-llm-wiki-operating-model.md`
- `wiki/pages/topics/slack-corpus-processing.md`
- `wiki/pages/topics/service-factory-rollout.md`
- `wiki/pages/topics/guardrails-team-swap.md`
- `wiki/pages/topics/observability-company-id-and-splunk.md`
- `wiki/pages/topics/windows-kubernetes-sql-connectivity.md`
- `wiki/pages/topics/agent-mcp-login.md`
- `wiki/pages/topics/azure-vpn-and-static-egress.md`
- `wiki/pages/decisions/prefer-grafana-when-splunk-trace-ingest-is-unreliable.md`
- `wiki/pages/decisions/security-review-required-for-agent-mcp-login.md`
- `wiki/pages/decisions/azure-dev-vpn-is-not-static-egress.md`
- `wiki/pages/questions/windows-sql-duplicate-name-root-cause.md`
- `wiki/pages/questions/remaining-slack-corpus-ingestion-queue.md`
- `wiki/sources/huddle-transcripts/2026-04-20-mark-service-factor-guardrails.md`
- `wiki/sources/huddle-transcripts/2026-04-13-devops-standup-observability.md`
- `wiki/sources/huddle-transcripts/2025-11-28-devops-standup-windows-sql.md`
- `wiki/sources/slack-api-chunks/platform-discussions-mcp-login-security-2026-04-13.md`
- `wiki/sources/slack-api-chunks/platform-discussions-azure-vpn-splunk-2025-10-02.md`
- `wiki/index.md`

Remaining work:

- Continue batch ingest by channel/topic; do not claim the full 2M-message corpus has been semantically compiled yet.
- Prioritize `team-devops`, `platform-discussions`, `ask-devops`, `ask-security`, and `pagerduty` high-signal threads.
