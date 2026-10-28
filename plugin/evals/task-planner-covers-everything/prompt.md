---
name: task-planner-covers-everything
tags: [agents, planning]
runs: 2
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Grep, Glob, Edit, Write, Bash, Agent]
---
REQ-001 "Refunds" is in Planning; the design is in docs/design/REQ-001/ and the test and release plans in
docs/plan/REQ-001/. Delegate to the agentic-sdlc:plan-task-planner subagent to write the task plan and load it.
Report the waves and the coverage check.
