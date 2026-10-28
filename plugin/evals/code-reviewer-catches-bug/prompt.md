---
name: code-reviewer-catches-bug
tags: [agents, development, review]
runs: 2
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Grep, Glob, Edit, Write, Bash, Agent]
---
Task REQ-001/T1 "refund limits" is ready for review: branch sdlc/REQ-001-T1, base sdlc/REQ-001, acceptance AC-2.
Delegate to the agentic-sdlc:dev-code-reviewer subagent and report its verdict.
