# Agentic SDLC: reference

The full reference. For a quick start, see the [README](../README.md).

A multi-agent, AI-native SDLC for any repo. Specialised Claude Code agents do the work at each stage; the developer orchestrates and approves human gates. Every action is recorded so a dashboard can show live what is happening.

```
/sdlc-init  →  /sdlc-new "title"  →  /sdlc-run REQ-001  →  🟡 gate  →  /sdlc-approve REQ-001  →  /sdlc-run REQ-001 …
```

## What's in the box

| Part | Where | What it does |
| --- | --- | --- |
| Engine (`sdlc` CLI) | `src/agentic_sdlc/` | Config, work-item state machine, human gates, task DAG with a parallel cap, event log |
| Claude Code plugin | `plugin/` | Slash commands, 8 stage agents, hooks that stream activity and guard the gates |
| Schemas | `schema/` | `sdlc.config.yaml`, work-item state and event line formats (stable for the dashboard and a future central service) |
| Dashboard | `dashboard/` (React) + `src/agentic_sdlc/dashboard/` (FastAPI) | Live web board: approvals inbox, pipeline, task DAG, live agents, activity, metrics |

## Install (once per machine)

```bash
# 1. The engine CLI (needs uv and Python 3.11+)
uv tool install --editable /path/to/agentic-sdlc

# 2. The plugin, from the local marketplace in this folder
claude plugin marketplace add /path/to/agentic-sdlc
claude plugin install agentic-sdlc@agentic-sdlc
#   or for one session while developing:  claude --plugin-dir /path/to/agentic-sdlc/plugin
```

## Use it in a repo

| Command | Who | What |
| --- | --- | --- |
| `/sdlc-init` | developer | Scans the repo, asks about stack, commands, gates, autonomy; writes `sdlc.config.yaml`, a `## Agentic SDLC` section in `CLAUDE.md`, templates and permissions |
| `/sdlc-new "Add refund API"` | developer | Suggests the work type, confirms it with you, and creates `REQ-001` and `specs/REQ-001.md` from that type's template |
| `/sdlc-run REQ-001` | developer | Runs the current stage's agents (parallel where possible) and stops at the next gate |
| `/sdlc-approve REQ-001 [comment]` | **human only** | Approves the waiting gate; the model cannot invoke it |
| `/sdlc-reject REQ-001 <what to change>` | **human only** | Sends the stage back; agents re-run with the feedback |
| `/sdlc-retype REQ-004 change <reason>` | **human only** | Changes the work type before Development starts; the item restarts at the new type's first stage |
| `/sdlc-status [REQ-001]` | anyone | Pipeline board, gates waiting, blocked tasks, recent activity |

The `sdlc` CLI does the same from a terminal: `sdlc status`, `sdlc log`, `sdlc next REQ-001`, `sdlc approve REQ-001 -m "ok"`.

## Work types

Every work item has a type. The type decides which of Requirements, Design and Development run, which agents run in Requirements, which spec template is used, and which gates apply.

| Type | Use it for | Stages | Requirements agents | Template |
| --- | --- | --- | --- | --- |
| `feature` (default) | A new capability | Requirements → Design → Planning → Development | req-analyst → req-nfr-analyst → req-ambiguity-checker | `specs/TEMPLATE.md`: all 8 requirement areas |
| `change` | Altering behaviour that exists | Requirements → Design and Planning (only if size M/L) → Development | req-change-impact-analyst → req-analyst → req-nfr-analyst → req-ambiguity-checker | `specs/TEMPLATE-change.md`: current vs desired, Replaces, Impact |
| `bug` | Something that should work does not | Triage (no gate) → Development | req-bug-triager | `specs/TEMPLATE-bug.md`: repro, root cause, regression AC |
| `task` | No behaviour change: upgrades, refactors, tooling | Development | none | `specs/TEMPLATE-task.md`: what and why, done when |

```
sdlc new "Expenses can be edited" --type change --updates REQ-001
sdlc new "List returns 500 on empty DB" --type bug --fixes REQ-001
sdlc new "Upgrade FastAPI" --type task
sdlc size REQ-002 S            # recorded in Requirements; a small change skips Design
sdlc retype REQ-003 change it works as specified   # human only, before Development starts
```

- **Links** (`--updates`, `--fixes`) give agents the linked spec and ADR. The reviewer checks that every linked acceptance criterion still holds, unless the change's spec lists it under **Replaces**.
- **No Design, no plan:** bugs, tasks and small changes become one task, `T1`, when Development starts. They still get TDD, a coder in a worktree and an independent review.
- **Type changes are human decisions.** The bug triager can say "not a bug, this is a change", but the hook blocks `sdlc retype` from models. Use `/sdlc-retype` or the dashboard's "Change type…" in the item view. The old spec text is kept at the end of the new spec.
- **Per-repo overrides** go in `sdlc.config.yaml` under `work_types`, e.g. give bug triage a gate or always run Design for changes:
  ```yaml
  work_types:
    bug:    {stages: [requirements, development], gates: {requirements: [tech-lead]}}
    change: {stages: [requirements, design, planning, development], design_if_size: []}   # [] = always run Design and Planning
  ```
  Stages after Development (CI/CD, QA, ...) follow `stages.<name>.enabled` for every type.

## Release trains, test environments, coverage, evals

- **Release trains.** `sdlc train new 2026.11 REQ-004 REQ-007` ships several items as one release: one version (the highest bump among them, applied once), combined notes (`sdlc train notes` → `docs/release/trains/2026.11.md`), one go/no-go (`sdlc train approve`, a person, approves every item's Release gate at once and only when all are waiting with matching versions), one production record and tag (`sdlc train deployed 2026.11 production --tag`, a person). An item in a train cannot be approved on its own.
- **Test environments.** `sdlc env up qa-functional --port 8101` starts an isolated copy of the app (every component, the frontend on port + 100 pointed at the backend) with its own data folder and waits until it answers; `sdlc env down` stops it and deletes the data; `sdlc env list`. Configure anything else (docker compose, scripts) with `test_env: {up, down, url, env}` and the placeholders `{name}`, `{port}`, `{data}`. The QA testers and the integration verifier use it instead of starting the app by hand.
- **Coverage.** A `coverage` command in `stack.commands` (or a component) runs in `sdlc check`; the line coverage is read from the report it writes (coverage.py JSON or XML, Istanbul/Jest/Vitest summary, lcov) or its printed TOTAL, and `stages.development.coverage_min: 80` fails the check below it. `sdlc detect` suggests the command for pytest-cov, Vitest and Jest.
- **Agent evals.** `plugin/evals/` holds cases for `claude plugin eval`: each builds a small repo and checks one agent behaviour (spec quality, root cause without fixing, IDOR caught, full plan coverage, a planted bug caught, never approving a gate). `scripts/run-evals.sh` runs them with safe defaults (1 run, no baseline, a $5 ceiling); see `plugin/evals/README.md`.

## Using it across a company

- **Versions.** The CLI and the plugin share one version (`sdlc --version`, now 0.7.0; see `CHANGELOG.md`). A project can pin what it needs with `requires: ">=0.7"` in `sdlc.config.yaml`; an older CLI then refuses to run and prints the upgrade command.
- **Company policy: `org.yaml`** (example: `docs/org.example.yaml`). Projects point at it with `org: <path>` or `SDLC_ORG_CONFIG`. `defaults` are where projects start; `policy` is enforced on every load and can only be tightened by a project: required stages, minimum gate approvers, compliance frameworks, required engine checks, required check commands (e.g. a secret scanner), a maximum model tier, human-only environments, pull requests, and the oldest supported Agentic SDLC. The org's `templates/` folder sits between the packaged templates and each project's copies.
- **`sdlc doctor`** checks a repo's setup: versions and pins, plugin vs CLI, company policy and what it changed, git and GitHub, tools on PATH, required checks, customised templates, the Copilot export, the dashboard build.
- **`sdlc portfolio`** summarises many projects (paths, or `projects:` in org.yaml): items per stage, gates waiting for people (oldest first), blocked items, open defects and release blockers, lead time, rework rate, AI cost. `--serve` opens the read-only portfolio dashboard.
- How to publish, install, pin and onboard: `docs/DISTRIBUTION.md`.

## Claude Code, GitHub Copilot and other AI tools

The engine, state, templates, checks and dashboard do not depend on the AI tool. Two things connect a tool to them:

- **MCP server: `sdlc mcp`** (stdio). 33 tools for every non-human `sdlc` command (`next_action`, `start_stage`, `complete_stage`, `lint_spec`, `load_plan`, `run_checks`, `pull_request`, `add_defect`, `deploy` to dev/staging …) and the orchestrator playbook as the prompt `sdlc_run`. Human decisions are not tools: `request_human_decision` files a pending request a person confirms. Each tool runs the same command, with the same engine checks, as in a shell.
- **One agent source.** The 32 agents are defined once in `plugin/agents/*.md`. Claude Code reads them through the plugin; `sdlc agents export --host copilot` (or `integrations.hosts: [claude, copilot]` at init) writes what Copilot reads in the repository:

| File | What |
| --- | --- |
| `.github/agents/<agent>.agent.md` | each agent as a Copilot custom agent (tools mapped to Copilot's `read`, `search`, `edit`, `execute`, `agent`, plus the `agentic-sdlc/*` MCP tools; `user-invocable: false` so only the orchestrator runs them), with host notes: no hooks here, worktrees made by hand for coders |
| `.github/agents/sdlc-orchestrator.agent.md` | the `/sdlc-run` playbook as a custom agent that delegates to the others with the `agent` tool, or does each step itself where sub-agents are not available |
| `.github/prompts/sdlc-run.prompt.md`, `sdlc-new`, `sdlc-status` | the commands in Copilot chat |
| `.vscode/mcp.json` | the `agentic-sdlc` MCP server (merged with servers already there) |
| `AGENTS.md` | the project rules section, as in `CLAUDE.md` |

Generated files carry a marker; re-exporting never overwrites a file a team wrote. Copilot picks the model from its model picker; to pin models, map the tiers: `models: {copilot: {opus: "<Copilot model name>", sonnet: "...", haiku: "..."}}`.

Differences to know: Copilot has no Agentic SDLC hooks, so live agent activity and usage tracking come only from Claude Code runs; the rules that matter are enforced by the engine and GitHub (human-only actions, production deploys, checks, PR statuses) and hold under both. The dashboard's "Run agents" button still starts Claude Code.

## Engine checks

Three checks run in the engine, so they hold whatever an agent does. `sdlc complete` refuses until they pass:

| Check | Command | What it checks | Blocks |
| --- | --- | --- | --- |
| Spec lint | `sdlc lint REQ-001` | every section of the type's template is present and written (not empty, not copied unchanged from the template, `n/a` only with a reason); at least one AC; each AC with Given/When/Then; a priority on each AC for features and changes; unique AC ids; warns about the header priority and unanswered blocking questions | completing Requirements (`stages.requirements.require_lint: false` turns it off) |
| Plan coverage | `sdlc plan REQ-001` | every `must` AC and every `SEC-n`, `OPS-n`, `TEST-n`, `REL-n` id defined in security.md, operations.md, test-plan.md and release-plan.md is in some task's `acceptance`; tasks that can run at the same time do not touch the same files; ACs referenced by tasks exist | completing Planning (`stages.planning.require_plan_check: false`) |
| Version | `sdlc version REQ-001 [--write] [--set v2.0.0]` | the next semantic version from the git tags and versions other items already claimed: feature or change → minor, bug or task → patch, breaking (release notes list breaking changes, or the spec says `breaking: yes`) → major (minor on 0.x); first release `v0.1.0` | completing Release while the release notes carry a different version |

The design and plan templates label the items that need code (`SEC-1:`, `OPS-1:`, `TEST-1:`, `REL-1:`), so the coverage check can count them.

## Models and AI cost

**A model per agent.** The most capable model only where judgement drives quality:

| Model | Agents |
| --- | --- |
| `opus` | design-architect, design-security-reviewer, dev-pr-reviewer |
| `haiku` | rel-notes-writer, rel-readiness-checker, qa-defect-triager |
| `sonnet` | every other agent, and the `/sdlc-run` orchestrator |

Each agent file declares its model, `sdlc next` returns it per step and the orchestrator passes it to the Agent tool. Aliases follow the newest model of each tier. Override per repo:

```yaml
models:
  orchestrator: sonnet
  agents: {dev-coder: opus, req-analyst: haiku}   # opus | sonnet | haiku | inherit | a claude-* model id
pricing:                                          # optional: your own prices, USD per million tokens
  claude-sonnet-5-5: {input: 2.00, output: 10.00, cache_write: 2.50, cache_read: 0.20}
```

**Usage and cost per work item.** When a subagent or the session stops, the plugin hook reads the Claude Code transcript (each message counted once) and records tokens per model, tagged with the work item, task, stage and agent. `sdlc usage [REQ-001]` and the dashboard (an "AI cost" tile, and an "AI usage" table per item by stage and agent) show tokens and an **estimated** cost: tokens × API list prices (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 per million input/output tokens, cache reads and writes priced separately; as of 2026-10-06). On a Claude subscription this is API-equivalent cost; usage counts against the plan's limits instead of being billed. Runs started from the dashboard also show the CLI's own reported cost.

## Human-only actions

Approving or rejecting a gate, changing a work type, skipping a stage, deferring or accepting a defect, unblocking a task and recording a production deploy are decisions people make. **The engine enforces this itself, whatever AI tool is running** (Claude Code, Copilot, a script):

- **In a terminal**, the command runs after you type the work item id to confirm (`--yes` skips the typing, still only in a terminal).
- **Without a terminal** (an agent's shell tool, a pipe, a headless run, a slash command's `!` line) nothing changes: the engine files a **pending request** and exits with code 3. A person confirms it under "Waiting for you" in the dashboard or with `sdlc confirm <id>` in a terminal (`sdlc pending` lists them, `--dismiss` drops one).
- **The dashboard's action buttons** need the per-launch token the server puts in the page plus the browser's same-origin header, so an agent calling the API with `curl` gets 403. Reading the API stays open.
- In Claude Code the guard hook also blocks these commands before they run; that is now the first layer, not the only one.

This stops unsanctioned agent actions under any host. It does not stop a program that deliberately fakes a terminal or the browser headers on your own machine; approvals tied to real identities (GitHub or SSO) are on the roadmap (`docs/PARKED.md`, P6).

## Frontend, backend and full-stack repos

A repo can have several **components**, each with its own folder and commands. `sdlc detect` finds them (`frontend/`, `backend/`, `apps/*`, `packages/*`, `services/*`) and `/sdlc-init` confirms them:

```yaml
components:
  backend:  {path: backend,  kind: backend,  commands: {install: uv sync, test: uv run pytest, lint: uv run ruff check ., run: uv run uvicorn app.main:app}}
  frontend: {path: frontend, kind: frontend, commands: {install: npm install, test: npm run test, lint: npm run lint, build: npm run build, e2e: npx playwright test, run: npm run dev}}
```

- A repo without `components` is one component at the root using `stack.commands`, as before.
- **Planning** gives every task a component; work that spans both is split (API task in backend, screen task in frontend, contract in api.md).
- **Coders** run their component's commands in its folder. **Frontend coders** build every state from ux.md, reuse the design system, stay accessible, and never put secrets in browser code.
- **Tests** are written at the right level per component: API and service tests for the backend; component tests (e.g. Testing Library) for screens, states and messages; end-to-end tests (e.g. Playwright) for journeys when the component has `e2e`.
- **Review** has a frontend checklist: states, design system, accessibility, XSS sinks, secrets in the bundle, effects and state, rendering cost, error handling, breakpoints.
- **`sdlc check`** runs every component's checks in its folder (`frontend: lint`, `backend: test`, `frontend: e2e`...).
- **Verification** starts every component (backend first, frontend pointed at it) and adds a browser smoke test of each UI journey: page loads, journey completes, no console errors or failed calls, the frontend calls exactly the API in api.md.
- **Docs** cover the frontend too: component stories or README, the screen and route map, which frontend settings are public.
- **QA** starts every component per tester (backend on its port, frontend on port + 100); a frontend component turns on the qa-ui-tester.

Frontend and backend in **separate repos** are not linked yet: Agentic SDLC needs one git repo at its root (branches, worktrees and commits), so each repo runs its own work items. Until the tracker integration links them, create the item in both repos with the same title, design the API contract in the backend item, and point the frontend item's spec at it.

## What a spec covers

One spec per work item covers every requirement area; the depth scales with the size (S: one line or `n/a: reason` per section, M/L: everything). Facts that hold for the whole product live once in `docs/product/` and every item adds to them. Traceability is generated, not written.

| Area | Feature spec section | Written by | Product-wide doc |
| --- | --- | --- | --- |
| 1. Business (problem, objectives and success metrics, benefits, scope, stakeholders) | 1, 2 Scope | req-analyst | `docs/product/overview.md` (objectives, stakeholder register) |
| 2. Users and functional (roles, user story, business rules BR-n, ACs AC-n with priority) | 2 | req-analyst | `overview.md` (personas and roles, glossary) |
| 3. Non-functional (performance, scale, availability, recovery, compatibility, accessibility) | 3 | req-nfr-analyst | |
| 4. Process and UX (today vs after, main, exception and failure flows, messages, wireframes) | 4 | req-analyst | |
| 5. Data and integrations (fields and sensitivity, owners, migration, retention, external systems) | 5 | req-nfr-analyst | `data.md` (data dictionary), `integrations.md` |
| 6. Security, privacy, compliance (authz, sensitive data, obligations, audit logging, abuse cases) | 6 | req-nfr-analyst; threat model in Design | `compliance.md` (seeded from `project.compliance`) |
| 7. Testing and acceptance (strategy, UAT, test data, Definition of done) | 7 | req-analyst | traceability: `docs/rtm/REQ-xxx.md` |
| 8. Delivery and governance (RAID, priority, estimate, approvals, decision log) | 8, Resolved decisions | req-analyst; gates by humans | `docs/rtm/REQ-xxx.md` (decision log, gate history) |

The req-ambiguity-checker turns every empty section, untouched placeholder or unexplained `n/a` into a question, and any unaddressed obligation from `project.compliance` into a **blocking** one. Change, bug and task templates keep the parts that fit: a change records a delta plus rollout and rollback, a bug records impact, data fix and whether it is a security issue, a task records risk and rollback.

`sdlc trace REQ-001` writes `docs/rtm/REQ-001.md`: each AC → tasks → test files (tests are tagged `REQ-001 AC-3`), then the decision log (resolved and answered questions) and gate history. Development runs it before finishing and sends `must` ACs without tests back to the dev-test-writer.

`sdlc templates` shows which of the repo's templates differ from Agentic SDLC's defaults; `sdlc templates --update spec.md` takes a newer default (commit your copy first).

## What a design covers

The design is a **pack** per work item, `docs/design/REQ-xxx/`. `README.md` is the overview and decision; each workstream has its own file and its own agent, so agents in the same group run in parallel without editing the same file. The architect ticks the workstreams an item needs: a small API change may get only `api.md`, `lld.md` and `security.md`.

| Workstream | File | Agent | Contents |
| --- | --- | --- | --- |
| Overview and decision | `README.md` | design-architect | context, 2-3 options, recommendation, high-level design and diagram, technology choices (licence, alternatives), ADRs, migration and rollout, rollback, cost and capacity, AC → design traceability, risks, review summary, decision |
| 1. Architecture | `README.md` + `docs/architecture/overview.md` | design-architect | system boundary and components (mermaid), tech stack, decisions index |
| 2. Database and data | `data.md` + `docs/architecture/data-model.md` | design-data-api-designer | ER diagram, schema, indexes and queries, lifecycle and retention, reversible migrations, consistency |
| 3. API and integration | `api.md` | design-data-api-designer | endpoints, OpenAPI 3.1 fragment, error model, auth, idempotency, pagination, versioning, integration timeouts and retries |
| 4. UI/UX | `ux.md` | design-ux-designer | screens and navigation, wireframes, every state, validation messages, responsive, WCAG 2.2 AA, localisation |
| 5. Detailed technical | `lld.md` | design-detail-designer | modules, interfaces, sequence diagrams, algorithms, error handling, **test design** |
| 6a. Security | `security.md` | design-security-reviewer | data flow with trust boundaries, STRIDE + OWASP, authz, secrets, sensitive data, audit, dependencies, compliance, verdict |
| 6b. Operations | `operations.md` | design-ops-designer | deployment, config and secrets, logs, metrics, traces, alerts, performance and scaling, failure modes, backups, RPO/RTO, runbook |

Added to the usual six: options with a recorded decision, migration/rollout/rollback, cost, technology licences, test design, and AC → design traceability (also in `sdlc trace`). The design gate asks "is this the right solution?"; the plan is approved separately at the Planning gate.

**ADRs are product-wide and numbered:** `sdlc adr REQ-001 "Use SQLite"` creates `docs/adr/0001-use-sqlite.md` (status `proposed`). Approving the design gate accepts the item's ADRs and writes the decision (who, when, comment) into the pack's README. Items designed earlier keep their single `docs/adr/ADR-REQ-xxx.md`.

The dashboard's file viewer draws the mermaid diagrams.

## What a plan covers

Planning runs after the design is approved, for features and changes of size M/L (bugs, tasks and small changes become one task `T1`). Every task is done by an agent, so the plan has no people, story points or sprints: it says what the agents build, in which order, how it is proven and how it ships. Its pack is `docs/plan/REQ-xxx/`; the test and release planners run in parallel, then the task planner turns everything into the task DAG.

| Planning activity | Where | Agent |
| --- | --- | --- |
| Scope confirmation (what ships, what doesn't, feature flag) | `README.md` §1 | plan-task-planner |
| Work breakdown and technical tasks (area, size, files; every AC, `SEC-n`, `OPS-n`, `TEST-n`, `REL-n`) | `README.md` §2-3, `.sdlc/plans/REQ-xxx.json` | plan-task-planner |
| Dependencies and sequencing (task DAG, waves from `sdlc plan`, other work items, external blockers) | `README.md` §4 | plan-task-planner |
| Effort and capacity (tasks, waves, critical path, retry risk) | `README.md` §5 | plan-task-planner |
| Test plan (coverage by level, regression, performance, security tests, UAT, data, entry/exit) | `test-plan.md` | plan-test-planner |
| Environment readiness, risk register with contingencies | `release-plan.md` | plan-release-planner |
| Release plan (CI/CD, flags, migration order, steps, rollback) | `release-plan.md` | plan-release-planner |
| Monitoring and post-release verification | `release-plan.md` | plan-release-planner |

**Dependencies between work items:** `sdlc new "Refunds" --blocked-by REQ-001` or `sdlc depends REQ-002 --on REQ-001` (the task planner records the ones it finds). A blocked item waits before Development until the other item's Development is approved; cycles are refused; the dashboard shows "Waits for REQ-001".

## What implementation covers

| Activity | How | Deliverable |
| --- | --- | --- |
| Branch management | `sdlc prepare` creates `sdlc/REQ-xxx` and commits SDLC files; each task gets `sdlc/REQ-xxx-Tn` in its own worktree | traceable branches, one merge commit per task |
| Coding | dev-coder per task, in parallel, following lld.md, api.md and data.md | working code |
| Unit testing | dev-test-writer writes failing AC tests first; each coder adds unit tests for its own logic, tagged `REQ-xxx AC-n` | unit and AC tests |
| Code review | dev-code-reviewer per task, separate context: correctness, tests, design, security, conventions, maintainability, performance, migrations and config | `.sdlc/reviews/REQ-xxx-Tn.md` verdicts |
| Static analysis and security | `sdlc check` runs every configured `lint`, `typecheck`, `test`, `build`, `audit`, `sast`, `secrets` command plus a built-in secret scan of changed files | `docs/dev/REQ-xxx/checks.md` |
| Integration | tasks merge into `sdlc/REQ-xxx` with the test suite run after each merge; the verifier checks the integrated build | merged branch |
| Developer testing | dev-integration-verifier runs the app and exercises every AC and UAT scenario end to end: happy, edge and error paths | `docs/dev/REQ-xxx/verification.md` |
| Database and configuration | versioned, reversible migrations (applied, rolled back and re-applied on a scratch DB); settings in `.env.example`; secrets from the environment only | migrations and documented config |
| CI pipeline | `sdlc check` is the local CI build; the **engine refuses to complete Development** until a check run passes after the last merge (`stages.development.require_checks`) | passing check report |
| Technical documentation | dev-doc-writer: API spec export, architecture overview, ADR consequences, configuration docs, runbook | updated `docs/` |
| Deployment to test | the verifier starts the app with `deploy_test`, `run` or `dev` from `stack.commands` | a running, testable build |
| Handover to QA | `sdlc handover` writes the build facts (branch, commit, how to run, changes, migrations, config, tasks and reviews, checks, coverage); the verifier adds how to verify, test data and known limitations | `docs/dev/REQ-xxx/handover.md` |

### Pull requests and commits

With `integrations.pull_requests: true` (needs a GitHub remote and `gh auth login`), each work item gets **one pull request**, `sdlc/REQ-xxx` into `main`. Per-task review stays local (a PR per task would be noise); the PR is where people review.

1. `sdlc pr REQ-001` commits the evidence, pushes **only** the work-item branch, and opens or updates the PR. Its body follows `.github/pull_request_template.md` (project-owned): why, what changed (code vs SDLC documents), how to test, checks, traceability, task reviews, tasks, risk and rollback, known limitations, and a reviewer checklist, with links to the spec, design, plan and handover on the branch. If the repo already has its own PR template, that one is kept and Agentic SDLC's evidence is added below it in a collapsed section.
2. **dev-pr-reviewer** reviews the whole PR (cross-task consistency, contract drift, completeness, risk) and posts line comments as a COMMENT review (`sdlc pr-review`).
3. People review and comment on GitHub as usual.
4. **dev-pr-responder** reads every open comment (`sdlc pr-comments`), turns each into a fix task or a reasoned reply, and after the fixes are built, reviewed and pushed, replies on each thread with the commit (`sdlc pr-reply`). It resolves only threads an agent opened; people resolve their own.
5. **Draft until released, and status checks GitHub can enforce.** The PR opens as a **draft** with a `sdlc: <stage>` label. Agentic SDLC posts two commit statuses on its head commit, `Agentic SDLC / QA` and `Agentic SDLC / Release`: pending until that gate is approved (failure while QA has open release blockers, success when the stage is off). New code pushed after an approval sets the status back to pending; commits that touch only SDLC documents keep it. When both are green the PR becomes **ready for review** with `sdlc: ready to merge`. `sdlc github protect` (a person, in a terminal) makes the base branch require both statuses, merged with checks already required (`--no-enforce-admins` to let admins override, `--reviews N` to also require approvals). GitHub then disables Merge until QA and Release pass. Branch protection on **private** repositories needs GitHub Pro, Team or Enterprise; without it the draft state and statuses still show, but do not block merging. `integrations.draft_prs` / `status_checks: false` turn the parts off.
6. Only people approve and merge. The hook blocks models from `git push`, `gh pr merge`, `gh pr review --approve` and any other `gh` write; the engine refuses to finish Development while the PR is behind the branch.

Agent comments start with a marker (`[agentic-sdlc · dev-pr-reviewer]`), because agents and people may share a GitHub account. GitHub does not let a PR's author approve it: if agents push with your account, a teammate approves on GitHub, or you approve the Development gate in the dashboard.

**Commit convention**: every commit names its work item and task, with trailers git and tools can read:

```
REQ-001/T3: add refund service

Why, and anything a reviewer should know.

Work-Item: REQ-001
Task: T3
Refs: AC-2, SEC-1
Generated-By: dev-coder
```

`sdlc check` includes a built-in "commit messages" check: every commit on the branch must start with `REQ-001:` or `REQ-001/Tn:`.

Problems found by `sdlc check` or the verifier become fix tasks (`sdlc fix REQ-001 "AC-3: 404 missing" --covers AC-3`) that go through a coder and a review like any other task; after two failed verification rounds the run stops and asks a human. `sdlc detect` suggests the run command and the security scanners installed on the machine (gitleaks, semgrep, bandit, pip-audit, npm audit).

## What testing covers

QA runs after the Development gate, on the work-item branch, **independently of Development**: the testers work from the requirements and the plan, not the code, each against its own instance of the app (own port, own data).

| Testing | Agent | Report |
| --- | --- | --- |
| Functional (every AC and business rule), API error states (validation, not found, dependency failures and timeouts), integration (endpoint → service → database, external systems), regression (linked items' ACs, full suite) | qa-functional-tester | `functional.md` |
| Security (each SEC-n control, OWASP API Top 10 attacks on a local instance, scanner results, compliance evidence) | qa-security-tester | `security.md` |
| Exploratory (time-boxed charters) and API compatibility (content types, clients, API versions) | qa-exploratory-tester | `exploratory.md` |
| **Only with a UI**, in real browsers (Playwright): UI (layout, controls, forms, labels, typography, alignment, consistency, screenshots), UX journeys (navigation, back, redirects, refresh, deep links), page loading (LCP, layout shift, skeletons, slow network), UI error states (simulated 500, timeout, offline, empty, validation, retry), accessibility (axe scan, keyboard-only, focus, names, contrast, form errors; WCAG 2.2 AA), compatibility (Chromium, Firefox, WebKit; mobile, tablet, desktop; real devices and WebViews listed for a person) | qa-ui-tester | `ui.md` + `screenshots/` |
| UAT (scenarios as the persona, business outcome, success metrics measurable) | qa-uat-tester | `uat.md` |
| Performance (NFR targets with p50/p95/p99, CPU and memory, concurrency and scalability steps, short soak), run alone | qa-performance-tester | `performance.md` |
| Defect review and triage (duplicates, severity, release blocker, fix tasks) | qa-defect-triager | `defects.md` |
| Test results review and sign-off (verdicts, defects, exit criteria, coverage) | engine (`sdlc test-report`) + human at the QA gate | `summary.md` |

An item "has a UI" when its design has `ux.md`, or the stack's framework is a UI framework; `stack.ui: true|false` in `sdlc.config.yaml` overrides it. A report counts only once its first line names one verdict (`Verdict: PASS`, `FAIL`, `PARTIAL` or `N/A`): an untouched template is "not filled" and fails the exit criteria.

**Defects** live in the work item: testers record them (`sdlc defect REQ-001 add "..." --severity high --blocker --covers AC-3`), the triager sets severity and blocker and starts fixes (`sdlc defect REQ-001 D1 fix` adds a fix task that goes through a coder and a reviewer), and the tester who found it retests (`verified` or `reopen`). **The engine refuses to complete QA while a release blocker is open, or if code was merged after the last `sdlc check`.** Deferring or accepting a defect is a human decision: the hook blocks models from it, and the dashboard has Defer / Accept / Reopen buttons. Approving the QA gate writes the sign-off (who, when, comment, open non-blocking defects) into `summary.md`.

## Release and deployment

**Agents deploy only to dev and staging. Production is always deployed by a person.** This is enforced, not just instructed: `sdlc deploy <REQ> production` refuses and prints the command for a person; `sdlc deployed` (recording a production deploy) and any configured production deploy/rollback command are blocked for models by the hook; deploy tools pointed at prod (`vercel --prod`, `kubectl … -n production`, `helm … values-prod`, `terraform apply … production` …) are blocked too; headless runs are never allowed production commands; and `agent_ok` cannot be set on production. Any environment other than dev and staging is human-only unless marked `agent_ok`.

```yaml
environments:
  dev:        {deploy: ./scripts/deploy.sh dev, url: http://localhost:8000, smoke: ["curl -fsS http://localhost:8000/health"]}
  staging:    {deploy: ./scripts/deploy.sh staging, rollback: ./scripts/rollback.sh staging, url: https://stg.example.com}
  production: {deploy: ./scripts/deploy.sh production, rollback: ./scripts/rollback.sh production, url: https://example.com}
```

| Release activity | How | Evidence |
| --- | --- | --- |
| Deploy to staging and check it | `sdlc deploy` + `sdlc smoke` (engine), then rel-deploy-verifier: health, journeys, errors and latency, logs, metrics and alerts, flags, success metric | `verification.md`, `smoke-staging.md` |
| Rollback readiness | rel-rollback-rehearser: deploy → migrate → roll back code and data → re-deploy, on staging or a local copy, timed | `rollback.md` |
| Release notes, changelog, version | rel-notes-writer: semantic version from what changed, Keep a Changelog entry | `release-notes.md`, `CHANGELOG.md` |
| Readiness | rel-readiness-checker: QA sign-off, PR approved and CI green, checks, migrations, flags, config and secrets, rollback, staging, runbook and alerts, notes, each with evidence | `readiness.md` |
| Release approval | the human Release gate; the engine refuses it until every verdict is PASS, staging passed its smoke check, and the version is set; approval is written into the release notes | |
| Production | a person merges the PR, deploys, then `sdlc deployed REQ-001 production --tag` (records it, tags `v1.4.0` on the base branch, pushes the tag) and `sdlc smoke REQ-001 production`; the stage completes only after a smoke check that ran after the deploy | `smoke-production.md` |

The per-item CI/CD stage is off by default: the pipeline is set up once per project (see `docs/PARKED.md`, P1).

Implementation (stage 4) is covered by the Development stage above: there are no pull requests because everything stays local; the per-task review file plus the Development gate play that role, and `sdlc check` plays the CI build.

## Stages and agents (MVP)

| Stage | Agents | Output | Gate |
| --- | --- | --- | --- |
| Requirements | req-analyst → req-nfr-analyst → req-ambiguity-checker (by type: see Work types) | `specs/REQ-xxx.md` with AC-1… and open questions | scope approval (none for bug triage) |
| Design | design-architect → (design-data-api-designer ∥ design-ux-designer ∥ design-ops-designer, only those needed) → (design-detail-designer ∥ design-security-reviewer) | design pack `docs/design/REQ-xxx/`, ADRs `docs/adr/NNNN-*.md` | design approval |
| Planning | (plan-test-planner ∥ plan-release-planner) → plan-task-planner | plan pack `docs/plan/REQ-xxx/`, `.sdlc/plans/REQ-xxx.json` | plan approval |
| Development | dev-test-writer (TDD) → dev-coder ×N in parallel worktrees → dev-code-reviewer per task → `sdlc check` → (dev-integration-verifier ∥ dev-doc-writer) → `sdlc handover` | branch `sdlc/REQ-xxx` with merged task branches, `.sdlc/reviews/`, `docs/dev/REQ-xxx/` (checks, verification, handover) | peer approval |
| QA | (qa-functional-tester ∥ qa-security-tester ∥ qa-exploratory-tester ∥ qa-uat-tester ∥ qa-ui-tester if there is a UI) → qa-performance-tester → qa-defect-triager → fix loop | QA pack `docs/qa/REQ-xxx/`: five reports, defects.md, summary.md | QA sign-off |
| Release | (rel-deploy-verifier ∥ rel-rollback-rehearser ∥ rel-notes-writer) → rel-readiness-checker, after `sdlc deploy <REQ> staging` | release pack `docs/release/REQ-xxx/`, `CHANGELOG.md` | release approval |
| Production | **a person deploys**; Agentic SDLC shows the steps, records the deploy (`sdlc deployed`), tags the version, runs the smoke check | `docs/release/REQ-xxx/smoke-production.md` | on-call |
| CI/CD (off per item) | manual; the pipeline is set up once per project | | per config |
| Monitoring and maintenance | **out of scope for now** (off by default; `docs/PARKED.md`, P5) | | |

**Parallelism:** agents in the same step group run together (design-architect and security reviewer); independent tasks from the DAG run as parallel coders, each in its own git worktree, capped by `stages.development.parallel_coders`; different work items can be at different stages at once.

**Guardrails:** a coder never reviews its own code; agents have narrow tool lists; a hook denies any model-initiated `sdlc approve`, `sdlc retype` or deferring/accepting a defect, hand edits to `.sdlc/state/` and force-pushes; a task that fails `max_attempts` times is blocked and handed to a human.

## Files it writes in your repo

```
sdlc.config.yaml        # the single config (edit freely; re-run /sdlc-init to regenerate CLAUDE.md section)
CLAUDE.md               # gets a ## Agentic SDLC section with commands and workflow rules
specs/REQ-xxx.md        # specs        docs/adr/ADR-REQ-xxx.md   # designs     docs/rca/  # RCAs
.sdlc/state/REQ-xxx.json  # work-item state (git-ignored: rewritten by every sdlc call)
.sdlc/plans/REQ-xxx.json  # task DAGs   .sdlc/reviews/  # review verdicts
.sdlc/events.jsonl        # live activity stream for the dashboard (git-ignored)
docs/product/             # product-wide: overview (stakeholders, personas), data dictionary, integrations, compliance
docs/design/REQ-xxx/      # design pack: README (options, decision) + data, api, ux, lld, security, operations
docs/architecture/        # product-wide: overview (components, stack, deployment), data model
docs/adr/NNNN-*.md        # numbered architecture decisions, accepted when the design is approved
docs/plan/REQ-xxx/        # plan pack: README (scope, tasks, dependencies), test-plan, release-plan
docs/dev/REQ-xxx/         # implementation evidence: checks.md, verification.md, handover.md
docs/qa/REQ-xxx/          # QA pack: functional, security, exploratory, uat, performance, defects, summary (sign-off)
docs/rtm/REQ-xxx.md       # traceability matrix and decision log, generated by `sdlc trace`
```

**Templates belong to your repo.** `/sdlc-init` copies the defaults once to `specs/TEMPLATE.md` (plus `TEMPLATE-change.md`, `TEMPLATE-bug.md`, `TEMPLATE-task.md`), `docs/adr/TEMPLATE.md` and `docs/rca/TEMPLATE.md`, and never overwrites them. Repos set up earlier get any missing ones on the next `sdlc new`. New specs and ADRs are made from your repo's copy, so a team can add or remove sections, such as "Compliance impact". Keep the `{req}` and `{title}` placeholders. In specs, keep the `**Answer:**` lines under open questions, because the agents rely on them. If a template file is deleted, the built-in default is used.

**Git:** at the start of Development, `sdlc prepare REQ-xxx` switches to the branch `sdlc/REQ-xxx` and commits the SDLC files from earlier stages: spec, ADR, plan and reviews. Agents never ask about this. It stops only when files outside SDLC paths are uncommitted, and its message tells you what to commit or stash. Coders branch `sdlc/REQ-xxx-Tn` from that branch in their own worktrees.

```
```

## Dashboard

```bash
cd your-repo && sdlc dashboard          # opens http://127.0.0.1:8765
```

- **Waiting for you**: every gate waiting for a decision, with the stage summary, a link to the files, and Approve / Send back (a comment is required to send back). Blocked tasks with *Unblock and retry*.
- **Pipeline**: every work item across the 8 stages; development shows tasks merged (e.g. `2/6`).
- **Work item detail**: stage stepper with who approved, what to do next, gate feedback, the task DAG drawn in waves (tasks in one wave run in parallel, with review verdicts), files (specs, ADRs, plans, reviews rendered in a viewer) and history.
- **Run agents from the UI**: *▶ Run agents* on a work item (or *Send back & run agents* on a gate) starts `/sdlc-run <REQ>` as a headless Claude Code process in the repo. Its log streams into the page, and *Stop* ends it. One run per work item; runs stop at the next gate, and approvals stay human (the guard hook still applies). Env: `SDLC_RUN_MODEL` picks the model, `SDLC_PLUGIN_DIR` the plugin folder, `SDLC_CLAUDE_BIN` the `claude` binary. Runs use the Claude Code login of whoever started `sdlc dashboard`.
- **Live agents**: subagents running right now, their REQ/task, last action and elapsed time.
- **Activity**: every engine and agent event, filterable by work item. Updates live over server-sent events.

It binds to localhost only. Approving in the UI is a human decision, recorded with your git user name.

Try it without running agents: `uv run python scripts/demo.py /tmp/sdlc-demo --live`, then `cd /tmp/sdlc-demo && sdlc dashboard`.

Change the UI: `cd dashboard && npm install && npm run dev` (proxies the API from a running `sdlc dashboard --no-open`); `npm run build` writes into the Python package.

## How status is tracked

The engine writes an event on every state change; plugin hooks write an event when the orchestrator dispatches an agent, when a subagent starts and stops, and for every edit or shell command inside it. Agents are tied to their work item by the Agent `description` (`REQ-001/T2 …`), so the dashboard can show "dev-coder · REQ-001/T2 · running tests" live. Formats are in `schema/`.

## Develop

```bash
uv sync --extra dev
uv run pytest            # engine, hook and guard tests
claude plugin validate .
```

Pilot project: `../sample-expense-api`.
