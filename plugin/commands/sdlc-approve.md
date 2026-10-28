---
description: Approve the gate a work item is waiting at (human decision). Usage /sdlc-approve REQ-001 [comment]
argument-hint: <REQ-xxx> [comment]
disable-model-invocation: true
allowed-tools: Bash(sdlc approve:*)
---

Approval recorded by the developer:

!`sdlc approve $ARGUMENTS 2>&1`

A slash command runs without a terminal, so the engine does not act on it directly: it files a request that only a person can confirm. If the output above says the request is waiting, tell the developer to click **Confirm** under "Waiting for you" in the dashboard, or run `sdlc confirm <id>` (or the same `sdlc` command) in their own terminal, where they type the work item id to confirm.

Tell the developer the result above in one line. If it succeeded and the next stage has agents, say they can continue with `/sdlc-run <REQ>`. If it failed, explain why from the message (for example the item is not waiting at a gate) and show `sdlc status <REQ>`. Do not run `sdlc approve` yourself.
