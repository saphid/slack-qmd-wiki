---
title: Prefer Grafana when Splunk trace ingest is unreliable
type: decision
status: active
updated: 2026-05-03
sources:
  - qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.md
---

# Prefer Grafana when Splunk trace ingest is unreliable

## Decision

If Grafana provides the full cross-service trace picture and Splunk/SignalFX trace ingestion remains unreliable, Grafana is acceptable as the practical investigation surface instead of continuing to sink time into Splunk trace delivery.

## Rationale

In the 2026-04-13 DevOps standup, Splunk trace delivery was described as timing out or failing to place traces correctly, while Grafana had the needed Displayr/Agentbase trace view and Oliver appeared happy to use it.

## Follow-up

Validate with actual trace users before retiring Splunk work completely.

Source: `qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.md` source_path=`/home/alex/Work/Docs/standup-transcripts-team-devops/2026-04-13_14-00_team-devops-standup.txt`
