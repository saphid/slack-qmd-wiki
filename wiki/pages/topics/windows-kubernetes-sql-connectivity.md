---
title: Windows Kubernetes SQL Connectivity
type: topic
status: active
updated: 2026-05-03
sources:
  - qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.md
---

# Windows Kubernetes SQL Connectivity

This topic tracks intermittent SQL connection failures when workloads run from Windows Kubernetes nodes.

First-pass synthesis:

- Failures were intermittent rather than constant.
- Observed errors were transport-level / TCP-provider style failures, including connection closure and a duplicate-name-on-network error.
- The investigation separated QServer/Sitero complexity from the network/SQL problem by also using a small .NET reproducer that repeatedly opened SQL connections.

Open question:

- [[pages/questions/windows-sql-duplicate-name-root-cause|Windows SQL duplicate-name root cause]]

Source: `qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.md` source_path=`/home/alex/Work/Docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.txt`
