---
description: Create a new Agentic SDLC work item (REQ-xxx) of the right type — feature, change, bug or task — and its spec file.
argument-hint: "<title of the work>"
---

Create a work item for: `$ARGUMENTS`

1. If `$ARGUMENTS` is empty, ask the developer for a one-line title and any notes (ticket text, customer request, error message, constraints).
2. **Suggest the type** from the title and notes, using these rules, and confirm it with the developer in one AskUserQuestion (your suggestion first, marked Recommended):
   - `feature`: a new capability users did not have.
   - `change`: alters behaviour that already exists ("now", "instead", "edit", "rename", "make X also Y").
   - `bug`: something that should already work does not (errors, wrong results, crashes).
   - `task`: technical work with no behaviour change (upgrades, refactors, CI, tooling).
3. For `change` and `bug`, look at `sdlc status` and ask which existing work item it updates or fixes, if any (offer the likely ones and "none").
4. Run `sdlc new "<title>" --type <type>` plus `--updates <REQ>` (change) or `--fixes <REQ>` (bug) when there is a link. It prints the id, the stages that will run and the spec path.
5. Put the developer's notes into the spec: for a bug, fill **Report**, **Steps to reproduce** and **Expected vs actual** from them; for a task, fill **What and why** and **Done when**; otherwise append them under a `## Source notes` heading at the end.
6. Reply in two lines: the id, type, stages that will run and spec path; then the next step, `/sdlc-run <REQ>`.
