# Changelog

Agentic SDLC follows semantic versioning. The CLI (`sdlc`) and the Claude Code plugin are released together with the
same version; `sdlc doctor` warns when they differ.

## [Unreleased]

### Added
- Screenshots in the README.
- ⏸ Pause and ▶ Resume for agent runs: Pause stops the agents and keeps their work; Resume makes interrupted
  tasks ready again and continues the same Claude Code conversation where it stopped. Stop still ends a run.

### Fixed
- Agents started outside the dashboard (e.g. from a terminal) count as running: the item no longer says "agents
  not running", the Live view shows the orchestrator directing, and their tasks are not reported as abandoned.
- A stopped run no longer leaves its interrupted sub-agents showing as working, or the item as Running.
- Task IDs keep their case in "needs you" messages.

## [0.8.0] - 2026-10-28

First public release, under the new name **Agentic SDLC** (was SDLC Pilot). The command is still `sdlc`.

### Changed
- Renamed to Agentic SDLC: package `agentic-sdlc`, plugin `agentic-sdlc` (slash commands `/agentic-sdlc:*`), PR
  statuses `Agentic SDLC / QA` and `Agentic SDLC / Release`. Projects set up before keep working: old CLAUDE.md
  sections, PR templates, handovers, PR comments and agent names are still recognised.
- The package carries the Claude Code plugin, so `uv tool install git+https://github.com/vks-app-dev/agentic-sdlc`
  works on any machine without a source checkout. The marketplace is now `agentic-sdlc`.
- Agents read and search files with their own tools and run one allowed command at a time, so unattended runs
  are not refused; the package manager's dependency commands (e.g. `uv lock`) are allowed.

### Added
- Dashboard Home: a request box that sorts a request into a feature, change, bug or task, links it to related
  items and shows the stages, agents and reviews ahead; resume the last job; the agent team with avatars; what
  each kind of work involves; a walkthrough of how agents work on a task.
- Board: each item's flow of stages with lead and sub-agent avatars, live status and review checkpoints; a Live
  view with the orchestrator in the centre and every stage around it; a Detail view of each sub-agent's actions
  and the handoffs; Pull request, Deployed and Checks cards; AI usage per item; a log that can always be shown.
- "Before agents can start": rules (no AI) find what a person must fix first (uncommitted setup files, the wrong
  branch, an empty spec, a task left in progress, a stuck working copy, commit titles) and offer a button for
  each; Run agents is held back until they are done. The agents' own requests for help come with next steps, the
  files to read, an Explain with AI button, and a decision box written to the spec's Decisions section.
- AI drafts of a spec for a person to review (Suggest with AI).
- Go live: after the Release approval an item shows Ready to go live, and Live once a person records the
  production deploy.
- Library: view, download, upload or reset any standard, skill or template; add your own standard or Claude Code
  skill and choose which agents follow it.
- Failed runs say why (an invalid key, an expired sign-in, no credit) and offer Sign in again and Retry.
- One dashboard per project on a fixed port; `sdlc dashboard` reuses a running one.

### Fixed
- Deploy and smoke commands that start a server in the background no longer hang.
- The pull request is pushed before its status checks are set.

## [0.7.0] - 2026-10-27

First version meant for use across several projects.

### Added
- Stages: Requirements (8 requirement areas, NFR analyst), Design (design pack with six workstreams, numbered
  ADRs), Planning (test plan, release plan, task DAG), Development (TDD, parallel coders, reviews, `sdlc check`,
  verification, docs, QA handover, PR), QA (functional, security, exploratory, UAT, UI, performance testers, defect
  triage, sign-off), Release (staging deploy and check, rollback rehearsal, notes, readiness) and Production
  (deployed by a person). Work types: feature, change, bug, task.
- Human-only actions enforced by the engine under any AI tool (terminal confirmation or the dashboard).
- A model per agent, and AI usage and estimated cost per item, stage, agent and model.
- Engine checks: spec lint, plan coverage, semantic versions.
- One pull request per work item: draft until Release, `Agentic SDLC / QA` and `Agentic SDLC / Release` statuses,
  `sdlc github protect`.
- MCP server (`sdlc mcp`) and Copilot export (`sdlc agents export --host copilot`).
- Company layer: `org.yaml` policy, `requires` version pins, `sdlc doctor`, `sdlc portfolio`.
- Dashboard home page: a welcome and request box (the request is sorted into a feature, change, bug or task
  and linked to related items, then confirmed by a person before agents start), the last unfinished job with
  Resume, and the agent team: one lead per stage with its sub-agents in the order they run.
- Standards and skills agents follow (coding, testing, security, API, git, code review, documentation, UI):
  Agentic SDLC defaults in `docs/standards/`, or the team's own files and Claude Code skills (`standards:` in the
  config, `sdlc standards use/reset`). `/sdlc-init` finds and asks for them, and for a folder of the team's own
  templates (`templates_from`). `sdlc next` lists each agent's standards. The dashboard's Library tab shows every
  standard, skill and template, whose it is and which agents use it.
- `sdlc upgrade`: adds settings newer versions introduced (keeping every value a repo set), rewrites the
  generated CLAUDE.md section and settings, adds missing templates; `--templates` replaces outdated templates.

## [0.1.0] - 2026-10-23
- MVP: Requirements, Design and Development with the plugin, engine and local dashboard.
