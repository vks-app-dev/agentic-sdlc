---
description: Show the Agentic SDLC pipeline board, gates waiting for approval, blocked tasks and recent agent activity.
argument-hint: "[REQ-xxx]"
allowed-tools: Bash(sdlc status:*), Bash(sdlc log:*)
---

!`sdlc status $ARGUMENTS 2>&1`

Recent activity:

!`sdlc log $ARGUMENTS -n 15 2>&1`

Show the board above to the developer as-is in a code block. Then add at most three lines: what needs their attention first (gates waiting, blocked tasks) and the command to act on it (`/sdlc-approve`, `/sdlc-reject`, `/sdlc-run`). If the output says no config was found, tell them to run `/sdlc-init`.
