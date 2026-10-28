---
name: bug-triager-finds-root-cause
tags: [agents, requirements, bug]
runs: 2
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Grep, Glob, Edit, Write, Bash, Agent]
---
Bug REQ-001 "Summary page crashes for new users" is in Triage. Delegate to the agentic-sdlc:req-bug-triager subagent
with the work item id and the spec path specs/REQ-001.md. Report its verdict and root cause.
