---
description: Change a work item's type (human decision), e.g. a bug that is really a change. Usage /sdlc-retype REQ-004 change <reason>
argument-hint: <REQ-xxx> <feature|change|bug|task> <reason>
disable-model-invocation: true
allowed-tools: Bash(sdlc retype:*)
---

Type change recorded by the developer:

!`sdlc retype $ARGUMENTS 2>&1`

A slash command runs without a terminal, so the engine does not act on it directly: it files a request that only a person can confirm. If the output above says the request is waiting, tell the developer to click **Confirm** under "Waiting for you" in the dashboard, or run `sdlc confirm <id>` (or the same `sdlc` command) in their own terminal, where they type the work item id to confirm.

Tell the developer the result above in one line. If it succeeded, say the item restarts at the stage named, its old spec text is kept at the end of the spec, and they can continue with `/sdlc-run <REQ>`. If it failed, explain why from the message (for example Development has already started). Do not run `sdlc retype` yourself.
