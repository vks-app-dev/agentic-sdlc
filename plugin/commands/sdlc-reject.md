---
description: Send the waiting gate back to the agents with feedback (human decision). Usage /sdlc-reject REQ-001 what to change
argument-hint: <REQ-xxx> <what to change>
disable-model-invocation: true
allowed-tools: Bash(sdlc reject:*)
---

Rejection recorded by the developer:

!`sdlc reject $ARGUMENTS 2>&1`

A slash command runs without a terminal, so the engine does not act on it directly: it files a request that only a person can confirm. If the output above says the request is waiting, tell the developer to click **Confirm** under "Waiting for you" in the dashboard, or run `sdlc confirm <id>` (or the same `sdlc` command) in their own terminal, where they type the work item id to confirm.

Tell the developer the result in one line. If it succeeded, say that `/sdlc-run <REQ>` will re-run this stage's agents with their feedback.
