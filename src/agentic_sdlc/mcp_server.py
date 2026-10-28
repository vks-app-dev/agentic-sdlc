"""Agentic SDLC as an MCP server, so any MCP-capable AI tool (GitHub Copilot in VS Code, the Copilot CLI or
coding agent, Cursor, Claude Code) can drive the same engine: `sdlc mcp` (stdio).

Every tool runs the same `sdlc` command an agent would run in a shell, with the same checks. The server has no
terminal, so the human-only actions (approve, reject, retype, skip, defer or accept a defect, unblock a task,
record a production deploy) never execute here: `request_human_decision` files a pending request that a person
confirms in the dashboard or with `sdlc confirm`. Production deploys are refused as they are everywhere else.
"""

import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import cli

from .paths import plugin_dir

PLAYBOOK = plugin_dir() / "commands" / "sdlc-run.md"

server = FastMCP(
    "agentic-sdlc",
    log_level="WARNING",
    instructions=(
        "Agentic SDLC runs a work item (REQ-xxx) through Requirements, Design, Planning, Development, QA and Release "
        "with specialised agents and human gates. Call `next_action` to see what to do, act on it, and record "
        "progress with these tools. Gates, work-type changes, skips, defect deferrals and production deploys are "
        "human decisions: use `request_human_decision` and tell the person, never try to work around it."),
)


def _run(*argv: str) -> str:
    """Run one `sdlc` command in-process and return what it printed, with its exit code when it failed."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main([a for a in argv if a is not None])
    text = (out.getvalue() + err.getvalue()).strip()
    return text if code == 0 else f"[exit {code}] {text}"


def _words(text: str) -> list[str]:
    return text.split() if text else []


# ---- reading ----

@server.tool()
def status(req: str = "") -> str:
    """Pipeline board: every work item and its stages, or one item with its tasks."""
    return _run("status", *([req] if req else []))


@server.tool()
def next_action(req: str) -> str:
    """What the orchestrator should do now for a work item (JSON): action, stage, agent steps, inputs, tasks."""
    return _run("next", req)


@server.tool()
def activity(req: str = "", lines: int = 30) -> str:
    """Recent events (agents, tools, gates) for one work item or all."""
    return _run("log", *([req] if req else []), "-n", str(lines))


@server.tool()
def usage(req: str = "") -> str:
    """AI tokens and estimated cost per item, stage, agent and model (JSON)."""
    return _run("usage", *([req] if req else []), "--json")


@server.tool()
def pending_human_decisions() -> str:
    """Requests waiting for a person to confirm."""
    return _run("pending")


# ---- work items and stages ----

@server.tool()
def new_item(title: str, type: str = "feature", updates: str = "", fixes: str = "", blocked_by: str = "") -> str:
    """Create a work item. type: feature | change | bug | task. Links are REQ ids."""
    argv = ["new", title, "--type", type]
    for flag, val in (("--updates", updates), ("--fixes", fixes), ("--blocked-by", blocked_by)):
        if val:
            argv += [flag, val]
    return _run(*argv)


@server.tool()
def start_stage(req: str) -> str:
    """Mark the work item's current stage as running (creates its design, plan, QA or release pack)."""
    return _run("start", req)


@server.tool()
def complete_stage(req: str, summary: str = "") -> str:
    """The agents finished the current stage: run the engine checks and wait at the gate (or advance)."""
    return _run("complete", req, *(["-m", summary] if summary else []))


@server.tool()
def record_file(req: str, path: str) -> str:
    """Record a file produced for the current stage."""
    return _run("artifact", req, path)


@server.tool()
def set_size(req: str, size: str) -> str:
    """Record the S/M/L size from Requirements (a small change then skips Design and Planning)."""
    return _run("size", req, size)


@server.tool()
def add_dependency(req: str, on: str) -> str:
    """Record that `req` waits for work item `on` before Development."""
    return _run("depends", req, "--on", on)


# ---- engine checks and packs ----

@server.tool()
def lint_spec(req: str) -> str:
    """Check the spec is complete: sections written, ACs with Given/When/Then and priority (JSON)."""
    return _run("lint", req)


@server.tool()
def load_plan(req: str) -> str:
    """Load .sdlc/plans/<REQ>.json: waves, critical path and coverage of ACs and design ids (JSON)."""
    return _run("plan", req)


@server.tool()
def design_file(req: str, workstream: str) -> str:
    """Create a design-pack file: data | api | ux | operations | lld | security."""
    return _run("design", req, workstream)


@server.tool()
def new_adr(req: str, title: str) -> str:
    """Create the next numbered ADR for a decision that outlives the work item."""
    return _run("adr", req, title)


@server.tool()
def run_checks(req: str) -> str:
    """Lint, typecheck, tests, build, audit, SAST and secret scans (the local CI build) (JSON)."""
    return _run("check", req)


@server.tool()
def trace(req: str) -> str:
    """Write the traceability matrix: AC → design → tasks → tests, decision log (JSON)."""
    return _run("trace", req)


@server.tool()
def handover(req: str) -> str:
    """Write the QA handover (build, how to run, changes, tasks, checks, coverage)."""
    return _run("handover", req)


@server.tool()
def test_report(req: str) -> str:
    """Write the QA summary and defect log; report exit criteria (JSON)."""
    return _run("test-report", req)


@server.tool()
def release_version(req: str, write: bool = False) -> str:
    """The release version from git tags, work type and breaking changes; write=true puts it in the notes."""
    return _run("version", req, *(["--write"] if write else []))


# ---- tasks and defects ----

@server.tool()
def task(req: str, task_id: str, action: str, note: str = "") -> str:
    """Update a task: start | review | done | fail (unblocking is a human decision)."""
    if action == "unblock":
        return "Unblocking a task is a human decision: use request_human_decision(action='unblock', ...)."
    return _run("task", req, task_id, action, *(["-m", note] if note else []))


@server.tool()
def add_fix_task(req: str, title: str, covers: str = "", after: str = "") -> str:
    """Add a fix task during Development or QA. covers/after: space-separated ids."""
    return _run("fix", req, title, *(["--covers", *_words(covers)] if covers else []),
                *(["--after", *_words(after)] if after else []))


@server.tool()
def add_defect(req: str, title: str, severity: str = "medium", blocker: bool = False, covers: str = "",
               found_by: str = "", steps: str = "") -> str:
    """Record a QA defect. severity: critical | high | medium | low."""
    return _run("defect", req, "add", title, "--severity", severity, "--blocker" if blocker else "--no-blocker",
                *(["--covers", *_words(covers)] if covers else []), *(["--found-by", found_by] if found_by else []),
                *(["-m", steps] if steps else []))


@server.tool()
def update_defect(req: str, defect: str, action: str = "update", severity: str = "", blocker: str = "",
                  note: str = "", duplicate_of: str = "") -> str:
    """Triage a defect: update | fix | verified | reopen | duplicate. Deferring or accepting is human."""
    if action in ("defer", "accept"):
        return "Deferring or accepting a defect is a human decision: use request_human_decision."
    argv = ["defect", req, defect, action]
    if severity:
        argv += ["--severity", severity]
    if blocker in ("true", "false"):
        argv += ["--blocker" if blocker == "true" else "--no-blocker"]
    if duplicate_of:
        argv += ["--of", duplicate_of]
    return _run(*argv, *(["-m", note] if note else []))


# ---- git, pull requests, deploys ----

@server.tool()
def prepare_branch(req: str) -> str:
    """Switch to sdlc/<REQ> and commit the SDLC files from earlier stages."""
    return _run("prepare", req)


@server.tool()
def pull_request(req: str) -> str:
    """Commit the evidence, push sdlc/<REQ> and open or update its draft pull request (JSON)."""
    return _run("pr", req)


@server.tool()
def pr_comments(req: str) -> str:
    """Unresolved review threads and comments on the work item's PR (JSON)."""
    return _run("pr-comments", req)


@server.tool()
def pr_review(req: str, review_json_file: str) -> str:
    """Post a COMMENT review from a JSON file {"summary", "comments": [{"path", "line", "body"}]}."""
    return _run("pr-review", req, "--file", review_json_file)


@server.tool()
def pr_reply(req: str, target: str, message: str, resolve: bool = False) -> str:
    """Reply to a review thread (PRRT_...) or comment id; resolve only threads an agent opened."""
    return _run("pr-reply", req, target, "-m", message, *(["--resolve"] if resolve else []))


@server.tool()
def deploy(req: str, env: str) -> str:
    """Deploy to dev or staging. Production is always deployed by a person and is refused."""
    return _run("deploy", req, env)


@server.tool()
def smoke(req: str, env: str) -> str:
    """Run an environment's smoke checks (dev or staging; production checks are run by a person)."""
    return _run("smoke", req, env)


@server.tool()
def env_up(name: str, port: int) -> str:
    """Start an isolated copy of the app (own port, own data) and wait until it answers (JSON with its URL)."""
    return _run("env", "up", name, "--port", str(port))


@server.tool()
def env_down(name: str) -> str:
    """Stop an environment started with env_up and delete its data."""
    return _run("env", "down", name)


@server.tool()
def log_event(req: str, event: str, detail: str = "") -> str:
    """Write an event to the activity log, e.g. human_needed with what the person must do."""
    return _run("event", event, "--req", req, *(["--detail", detail] if detail else []))


# ---- human decisions: filed, never executed here ----

@server.tool()
def request_human_decision(action: str, req: str, comment: str = "", value: str = "") -> str:
    """Ask a person to decide. action: approve | reject | retype (value: new type) | skip | defer | accept
    (value: defect id) | unblock (value: task id). Files a pending request; a person confirms it."""
    argv = {"approve": ["approve", req, *(["-m", comment] if comment else [])],
            "reject": ["reject", req, "-m", comment or "see the work item"],
            "retype": ["retype", req, value, "-m", comment or "type change requested"],
            "skip": ["skip", req, "-m", comment or "skip requested"],
            "defer": ["defect", req, value, "defer", "-m", comment or "defer requested"],
            "accept": ["defect", req, value, "accept", "-m", comment or "accept requested"],
            "unblock": ["task", req, value, "unblock"]}.get(action)
    if argv is None:
        return "Unknown action: approve, reject, retype, skip, defer, accept or unblock."
    return _run(*argv)


@server.prompt()
def sdlc_run(req: str) -> str:
    """The orchestrator playbook for one work item: run its current stage's agents until a human gate."""
    body = PLAYBOOK.read_text().split("---", 2)[-1] if PLAYBOOK.exists() else ""
    return (f"Work item: {req}. Use the Agentic SDLC MCP tools for every `sdlc` command below (next_action for "
            f"`sdlc next`, start_stage for `sdlc start`, and so on).\n\n" + body.replace("$0", req)
            .replace("$ARGUMENTS", req))


def main(root: str | None = None) -> None:
    if root:
        os.chdir(root)
    os.environ["SDLC_VIA"] = "MCP"  # pending human requests say where they came from
    server.run("stdio")


if __name__ == "__main__":  # python -m agentic_sdlc.mcp_server
    main()

