---
title: 2026-04-13 DevOps standup — traces, baggage, and Splunk
type: source-summary
status: active
updated: 2026-05-03
sources:
  - qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.md
---

# 2026-04-13 DevOps standup — traces, baggage, and Splunk

This standup records two related observability threads:

- Trace delivery to Splunk/SignalFX was unreliable while Grafana had the cross-service trace picture people needed.
- The team considered whether Grafana was good enough for trace investigation instead of continuing to spend time forcing Splunk trace ingestion.
- The same meeting mentions missing baggage/company-context propagation in a code path connected to Google Translate / company ID work.

Durable points:

- Grafana may be the practical trace investigation surface when Splunk trace ingestion is unreliable.
- Baggage/company-context propagation needs explicit proof rather than assuming the value is present.

Links:

- [[pages/topics/observability-company-id-and-splunk|Observability, Company ID, and Splunk]]
- [[pages/decisions/prefer-grafana-when-splunk-trace-ingest-is-unreliable|Prefer Grafana when Splunk trace ingest is unreliable]]

Source: `qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.md` source_path=`/home/alex/Work/Docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.txt`
