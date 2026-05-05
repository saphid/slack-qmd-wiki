---
title: Windows SQL duplicate-name root cause
type: question
status: open
updated: 2026-05-03
sources:
  - qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.md
---

# Windows SQL duplicate-name root cause

## Question

What is the root cause of intermittent SQL transport failures and the duplicate-name-on-network error from Windows Kubernetes nodes?

## Known evidence

- The failures are intermittent and transport-level.
- The duplicate-name error wording does not clearly identify whether the duplicate is the client/container identity, endpoint, DNS name, or another Windows networking concept.
- A small .NET repro app was used alongside QServer/C code to narrow the issue.

## Next evidence to find

- Slack/API chunks or tickets that record the eventual fix or non-fix.
- Kubernetes/Windows node configuration notes.
- Any follow-up standups after 2025-11-28 mentioning the duplicate-name error.

Source: `qmd/huddle-transcripts/vm-work-docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.md` source_path=`/home/alex/Work/Docs/standup-transcripts-team-devops/2025-11-28_11-00_team-devops-standup.txt`
