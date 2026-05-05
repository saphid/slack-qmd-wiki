---
title: Observability, Company ID, and Splunk
type: topic
status: active
updated: 2026-05-03
sources:
  - qmd/huddle-transcripts/vm-work-docs/huddle-transcripts-mark/2026-04-20_15-02_huddle-with-mark.md
  - qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.md
---

# Observability, Company ID, and Splunk

Several DevOps conversations revolve around whether the right contextual fields and traces reach Splunk/SignalFX versus Grafana.

Durable synthesis:

- Company ID in logs was an explicit verification target for service/baggage work.
- Missing baggage/company-context propagation can invalidate assumptions about whether downstream logs contain the needed company context.
- Splunk trace ingestion was unreliable enough that Grafana was considered a sufficient trace investigation surface if it provided the full cross-service picture.
- Evidence-backed verification is preferred over relying on someone else's assertion that context is already in baggage.

Related decisions:

- [[pages/decisions/prefer-grafana-when-splunk-trace-ingest-is-unreliable|Prefer Grafana when Splunk trace ingest is unreliable]]

Source: `qmd/huddle-transcripts/vm-work-docs/huddle-transcripts-mark/2026-04-20_15-02_huddle-with-mark.md` source_path=`/home/alex/Work/Docs/huddle-transcripts-mark/2026-04-20_15-02_huddle-with-mark.txt`
Source: `qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.md` source_path=`/home/alex/Work/Docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.txt`
