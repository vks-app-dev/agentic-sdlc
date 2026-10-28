---
name: security-reviewer-flags-idor
tags: [agents, design, security]
runs: 2
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Grep, Glob, Edit, Write, Bash, Agent]
---
REQ-001 "Monthly statements" is in Design and its design pack is in docs/design/REQ-001/. Delegate to the
agentic-sdlc:design-security-reviewer subagent to write docs/design/REQ-001/security.md. Report its verdict.
