---
title: 2025-11-28 DevOps standup — Windows SQL connection investigation
type: source-summary
status: active
updated: 2026-05-03
sources:
  - qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.md
---

# 2025-11-28 DevOps standup — Windows SQL connection investigation

This standup contains context for intermittent SQL connection failures on Windows Kubernetes nodes, including transport-level failures and a duplicate-name-on-network error. The investigation used both production-like C/QServer code and a simpler .NET repro app to isolate whether the issue was broader than QServer.

Durable points:

- The problem was intermittent SQL connectivity from Windows nodes in Kubernetes.
- Errors were transport-level rather than ordinary login failures.
- A smaller .NET app was useful as a reproducer because it removed unrelated QServer/Sitero complexity.

Links:

- [[pages/topics/windows-kubernetes-sql-connectivity|Windows Kubernetes SQL Connectivity]]
- [[pages/questions/windows-sql-duplicate-name-root-cause|Windows SQL duplicate-name root cause]]

Source: `qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.md` source_path=`/home/alex/Work/Docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.txt`
