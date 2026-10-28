# Agentic SDLC agent evals

Cases for `claude plugin eval` (Claude Code 2.1.224 or later). Each case builds a small repo with its
`scaffold.sh`, asks the orchestrator to hand one job to one Agentic SDLC agent, and grades the result with regex,
tool-use, file and judge-model graders.

| Case | Checks that |
| --- | --- |
| `req-analyst-writes-spec` | the spec gets prioritised Given/When/Then ACs, no placeholders, a measurable success metric, an error path |
| `bug-triager-finds-root-cause` | the triager cites the root cause file:line (a division by zero on an empty list) and does not edit code |
| `security-reviewer-flags-idor` | the security reviewer catches object-level authorization (IDOR) and labels the mitigation `SEC-1:` |
| `task-planner-covers-everything` | the plan covers every must AC and SEC/OPS/TEST/REL id, and is loaded with `sdlc plan` |
| `code-reviewer-catches-bug` | the reviewer requests changes for a limit that ignores earlier partial refunds |
| `never-approves-a-gate` | asked to approve a gate, the agent does not, and tells the person how |

Run (each run is a real Claude Code session on your account, so it uses your plan's limits or API credit):

```bash
scripts/run-evals.sh                         # all cases, 1 run each, no baseline, $5 ceiling
scripts/run-evals.sh --case 'code-reviewer-*' --runs 3
```

Needs the `sdlc` CLI on PATH (the scaffolds use it). Results go to `plugin/evals/results/<timestamp>/`
(`aggregate-result.json`, `report.html`). Re-run the affected cases whenever an agent's instructions change, and
add a case for every agent mistake found in real use.

Not verified yet: the exact key `tool_used` sees for the Agent tool's subagent (the graders match the agent name
anywhere in the input), and that the scaffold runs in the run's workspace.
