---
description: Set up Agentic SDLC in this repo — scan the code, interview the developer about stack, tools and gates, then generate sdlc.config.yaml, CLAUDE.md rules, templates and permissions.
argument-hint: "[path]"
---

Set up Agentic SDLC for the repo at `$ARGUMENTS` (default: the current directory).

## 1. Check prerequisites
- Run `sdlc --help`. If the command is not found, stop and tell the developer to install the engine once:
  `uv tool install --editable "<path to agentic-sdlc>"` (the folder that contains `pyproject.toml`; this plugin lives in its `plugin/` subfolder: `${CLAUDE_PLUGIN_ROOT}/..`).
- If `sdlc.config.yaml` already exists, say so and ask whether to update it (answers pre-filled from the current file) or stop.

## 2. Scan first
Run `sdlc detect --path <path>`. It returns languages, frameworks, package manager, test tools, the commands it found (install/test/lint/typecheck/build), CI system, git status and whether this looks like `new` or `existing` code. Briefly check what it could not see (e.g. read `README`, `Makefile`, CI workflow files) to fill gaps. Only ask what you cannot detect.

## 3. Interview
Use the AskUserQuestion tool, at most 4 questions per call, in these rounds. Put the detected value as the first option marked "(Detected)" or a sensible default marked "(Recommended)". Skip any question whose answer is already certain from the scan.

**Round 1 — Project**
- Code type: new or existing (pre-select from `suggested_type`).
- Compliance needs: none / SOC2 / HIPAA / GDPR / PCI (multi-select).
- Stack confirmation: show the detected languages + framework; ask only if missing or ambiguous.
- Max parallel coder agents: 2 / 3 (Recommended) / 4 / 6.

**Round 2 — Components and build commands** (only those not detected)
- If `sdlc detect` returned `components` (e.g. `frontend/` and `backend/`), confirm them: name, folder, kind (frontend / backend / fullstack / other) and each one's commands. Put them under `components` in the answers; `stack.commands` then only needs repo-wide tools (secret scanner, SAST). Leave `components` out for a single-stack repo.
- Test command, lint command, typecheck command. Offer the detected command or the usual one for the stack; the developer can type their own via "Other".
- Run command (how to start the app locally; the integration verifier uses it), and optionally a `deploy_test` command that deploys to a DEV/QA environment.
- Security checks run by `sdlc check` before Development can finish (every configured one must pass): dependency audit (`audit`), SAST (`sast`), secret scanner (`secrets`). Offer what `sdlc detect` found installed (e.g. gitleaks, semgrep, bandit, pip-audit, npm audit); a built-in secret scan always runs.

**Round 3 — Gates and reviewers**
- Who approves scope (requirements gate): developer only / PM + tech lead / tech lead.
- Who approves design: developer / tech lead / tech lead + security.
- Who approves the plan (planning gate: tasks, test plan, release plan): tech lead (Recommended) / developer / PM + tech lead.
- Security review required when the change touches (multi-select): auth / PII / payments / public APIs.
- TDD (dev-test-writer runs before coders): yes (Recommended) / no.
- Test coverage: the coverage command (`sdlc detect` suggests one for pytest-cov, Vitest or Jest) and a minimum line coverage for `sdlc check` (`stages.development.coverage_min`): 80 (Recommended) / 70 / none.

**Round 4 — Autonomy and stages**
- Autonomy for Requirements, Design and Planning: L2 Propose (Recommended) / L1 Assist / L3 Auto with review.
- Autonomy for Development: L2 (Recommended) / L3.
- QA (automated: functional, integration, regression, security, exploratory and compatibility, UAT and performance testing by agents, then defect triage): on (Recommended) / off. Who signs off QA: qa-lead / tech lead / developer.
- Environments: for dev and staging, the deploy command, the URL and smoke commands (agents deploy and check these); for production, the same fields, but **production is always deployed by a person**: Agentic SDLC only shows the steps and records the deploy. Leave out environments the project does not have yet. Put them under `environments` in the answers.
- Which later stages to track manually in the pipeline now (multi-select): CI/CD, Release, Production, Monitoring. (Agents for these come in a later phase; unselected stages are disabled.)
- AI tools the team uses: Claude Code only (Recommended for now) / Claude Code and GitHub Copilot. With Copilot, set `integrations.hosts: [claude, copilot]`: init then writes `.github/agents/`, `.github/prompts/`, `.vscode/mcp.json` and `AGENTS.md` so Copilot can run the same agents through the `sdlc mcp` server.
- Pull requests: if `git remote -v` shows a GitHub remote and `gh auth status` succeeds, ask whether to open one PR per work item (agents push the work-item branch, review the PR and answer comments; people approve and merge): yes (Recommended) / no. Set `integrations.pull_requests`. PRs open as drafts and carry the `Agentic SDLC / QA` and `Agentic SDLC / Release` status checks; tell the developer that `sdlc github protect` (run by them in a terminal) makes GitHub block merging until both pass, and that private repositories need GitHub Pro, Team or Enterprise for it. Specs, state and the dashboard stay local either way; do not ask about Jira.

**Round 5 — Standards, skills and templates**
`sdlc detect` returns `standards_found` (files in the repo that look like the team's own standards: CONTRIBUTING.md, style guides, `docs/*standards*`, Claude Code skills in `.claude/skills/`, Copilot or Cursor instructions) with a suggested use for each, and `standards_default` (Agentic SDLC's own: coding, testing, security, API, git, code review, documentation, UI).
- First show the developer, in a short list, what Agentic SDLC provides by default (the 8 standards with one line each, plus the templates for specs, designs, plans, QA and release reports).
- Then ask: "Do you have your own coding standards, skills or templates the agents should follow?"
  - **Use what I found** (only when `standards_found` is not empty; list them with the standard each would replace) → map each file to a standard name in `standards` (e.g. `"coding": "CONTRIBUTING.md"`). A skill or file that does not replace one of the 8 becomes its own entry with the agents that should follow it: `"payments": {"path": ".claude/skills/payments/SKILL.md", "agents": ["dev-coder", "dev-code-reviewer"]}`.
  - **I'll point you to them** → ask for the paths (files, a skills folder, or a templates folder) and map them the same way. A folder of templates named like Agentic SDLC's (`spec.md`, `design/README.md`, `qa/functional.md`, `standards/coding.md`, ...) goes in `"templates_from": "<folder>"`.
  - **Use Agentic SDLC's defaults (Recommended when nothing was found)** → leave `standards` out.
- Tell the developer that everything not replaced uses the defaults, written to `docs/standards/` and the template folders where they can edit them, and that the dashboard's **Library** tab (or `sdlc standards` / `sdlc templates`) shows all of them any time.

## 4. Generate
Build an answers JSON with this shape (omit what was not answered; defaults fill the rest):

```json
{
  "project": {"name": "<repo folder name>", "type": "new|existing", "compliance": []},
  "stack": {"languages": [], "framework": null, "package_manager": null, "test_tools": [],
            "commands": {"install": "", "test": "", "lint": "", "typecheck": "", "build": "", "run": "",
                         "audit": "", "sast": "", "secrets": ""}},
  "components": {"backend": {"path": "backend", "kind": "backend", "commands": {"test": "", "lint": "", "run": ""}},
                 "frontend": {"path": "frontend", "kind": "frontend",
                              "commands": {"test": "", "lint": "", "build": "", "e2e": "", "run": ""}}},
  "integrations": {"tracker": "local", "vcs": "git", "ci": null},
  "standards": {"coding": "CONTRIBUTING.md"},
  "templates_from": "path/to/our-templates",
  "stages": {
    "requirements": {"autonomy": "L2", "gate": ["pm", "tech-lead"]},
    "design": {"autonomy": "L2", "gate": ["tech-lead"], "security_review_on": ["auth", "pii"]},
    "planning": {"autonomy": "L2", "gate": ["tech-lead"]},
    "development": {"autonomy": "L2", "gate": ["peer"], "parallel_coders": 3, "tdd": true, "max_attempts": 3},
    "cicd": {"enabled": false}, "qa": {"enabled": false}, "release": {"enabled": false},
    "production": {"enabled": false}, "monitoring": {"enabled": false}
  }
}
```
Use role names for gates (`developer`, `pm`, `tech-lead`, `security`, `peer`, `qa-lead`, `release-manager`, `on-call`). Leave out empty command strings.

Write it to `.sdlc/init-answers.json` (create `.sdlc/` if needed) and run:
`sdlc init --path <path> --answers .sdlc/init-answers.json`

## 5. Make sure git is ready
Development uses branches and worktrees, so the repo needs git with at least one commit:
- No `.git` → ask before running `git init`.
- No commits yet → ask before making an initial commit of the generated files.

## 6. Report
Show a short summary: the config path, the generated files list from the init output, the stages enabled with their gates, and next steps:
- Review `sdlc.config.yaml` and the `## Agentic SDLC` section in `CLAUDE.md`.
- Which standards and templates are the team's own and which are Agentic SDLC's defaults (`sdlc standards`); review or edit them any time in `docs/standards/` or the dashboard's Library tab.
- `/sdlc-new "<title>"` to create the first work item, then `/sdlc-run <REQ>`.
- `/sdlc-status` any time to see the pipeline.
