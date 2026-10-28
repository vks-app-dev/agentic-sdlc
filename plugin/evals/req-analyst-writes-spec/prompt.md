---
name: req-analyst-writes-spec
tags: [agents, requirements]
runs: 2
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Grep, Glob, Edit, Write, Bash, Agent]
---
Work item REQ-001 "Refunds for card payments" is in the Requirements stage. Delegate to the agentic-sdlc:req-analyst
subagent to fill specs/REQ-001.md (the developer's notes are at the end of the spec). Do not write the spec
yourself. When it returns, run `sdlc lint REQ-001` and report the result.
