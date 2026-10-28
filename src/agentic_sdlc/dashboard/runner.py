"""Start `/sdlc-run <REQ>` as a headless Claude Code process from the dashboard.

One run per work item at a time. Output goes to .sdlc/run/logs/<run-id>.log; start and finish are
written to the event log so every open dashboard sees them. Gates stay human: the plugin's guard
hook still denies `sdlc approve` inside the run.
"""

import json
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config, events, release
from ..state import UI_FRAMEWORKS

# Dependency work (upgrades, adding a library) needs the package manager's own commands, not only the project's
# configured ones. Only these subcommands: nothing that publishes, logs in or changes global state.
PACKAGE_MANAGER_COMMANDS = {
    "uv": ("uv lock", "uv add", "uv remove", "uv sync", "uv tree", "uv pip list", "uv pip show"),
    "npm": ("npm install", "npm ci", "npm uninstall", "npm ls", "npm outdated", "npm view"),
    "pnpm": ("pnpm install", "pnpm add", "pnpm remove", "pnpm ls", "pnpm outdated"),
    "yarn": ("yarn install", "yarn add", "yarn remove", "yarn list", "yarn outdated"),
    "poetry": ("poetry lock", "poetry add", "poetry remove", "poetry install", "poetry show"),
    "pip": ("pip install", "pip list", "pip show", "pip index versions"),
}

from ..paths import plugin_dir

DEFAULT_PLUGIN_DIR = plugin_dir()


@dataclass
class Run:
    id: str
    req: str
    command: list[str]
    log: str
    started: str
    pid: int | None = None
    status: str = "running"  # running | finished | failed | stopped
    exit_code: int | None = None
    ended: str | None = None
    cost_usd: float | None = None
    error: str | None = None  # why it failed, in words a person can act on
    summary: str | None = None  # the run's own last words: where it stopped and why
    _proc: subprocess.Popen | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in ("id", "req", "log", "started", "pid", "status", "exit_code", "ended",
                                           "cost_usd", "error", "summary")}
        d["command"] = shlex.join(self.command)
        return d


class Runner:
    def __init__(self, root: Path, cfg_loader):
        self.root = root
        self.cfg = cfg_loader
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()
        self.logs = root / ".sdlc" / "run" / "logs"
        self._load_past()

    def _load_past(self) -> None:
        """Runs from earlier dashboard sessions, from their logs, so an item still shows how its last run ended."""
        if not self.logs.exists():
            return
        for log in sorted(self.logs.glob("REQ-*.log")):
            run_id = log.stem
            req = "-".join(run_id.split("-")[:2])
            text = log.read_text(errors="replace")
            result = next((json.loads(ln) for ln in reversed(text.splitlines())
                           if '"type":"result"' in ln.replace(" ", "") and ln.startswith("{")), None)
            ended = events.now() if result is None else None
            stamp = run_id.split("-", 2)[2]  # 20261027-203814, local time when the run started
            from datetime import datetime, timezone

            # the same form as events.now() (UTC), so past and new runs sort together
            started = datetime.strptime(stamp, "%Y%m%d-%H%M%S").astimezone(timezone.utc).isoformat(timespec="seconds")
            status = "stopped" if result is None else "failed" if result.get("is_error") else "finished"
            run = Run(run_id, req, [], str(log.relative_to(self.root)), started, status=status,
                      ended=ended or started, cost_usd=reported_cost(log))
            run.summary = final_words(log)
            if status == "failed":
                run.error = failure_reason(log)
            self.runs[run_id] = run

    def command(self, req: str) -> list[str]:
        """The headless Claude Code call. SDLC_CLAUDE_BIN overrides the binary (tests use a fake)."""
        binary = os.environ.get("SDLC_CLAUDE_BIN", "claude")
        cfg = self.cfg()
        cmds = [*(cfg.get("stack", {}).get("commands") or {}).values(),
                *(v for c in config.components(cfg) for v in c["commands"].values()),
                *(v for name, e in release.environments(cfg).items() if not release.human_only(cfg, name)
                  for v in [e.get("deploy"), e.get("rollback"), *(e.get("smoke") or [])] if v)]
        blocked = set(release.guard_rules(cfg)["commands"])  # production commands are never allowed headless
        cmds = [c for c in cmds if c not in blocked]
        # Verification starts the app and calls it, and docs export the API spec: allow the project's own
        # runner prefix (e.g. "uv run", "npm run") and curl, nothing broader.
        runners = sorted({" ".join(c.split()[:2]) for c in cmds if len(c.split()) > 1 and c.split()[1] == "run"})
        allowed = ["Read", "Grep", "Glob", "Edit", "Write", "Agent", "Bash(sdlc:*)", "Bash(git:*)", "Bash(curl:*)",
                   "Bash(ps:*)", "Bash(cd:*)", *(f"Bash({c}:*)" for c in dict.fromkeys(cmds)),
                   *(f"Bash({r}:*)" for r in runners)]
        stack = cfg.get("stack", {})
        managers = {stack.get("package_manager")} | {c.get("package_manager") for c in (cfg.get("components") or {}).values()}
        allowed += [f"Bash({c}:*)" for m in sorted(filter(None, managers)) for c in PACKAGE_MANAGER_COMMANDS.get(m, ())]
        if (stack.get("ui") or (stack.get("framework") or "") in UI_FRAMEWORKS
                or any(c["kind"] in ("frontend", "fullstack") for c in config.components(cfg))):
            allowed += ["Bash(npx playwright:*)"]  # browser smoke tests and the UI tester
        cmd = [binary, "-p", f"/agentic-sdlc:sdlc-run {req} --auto", "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits", "--allowedTools", *allowed]
        found = plugin_dir()
        if found.exists():
            cmd += ["--plugin-dir", str(found)]
        cmd += ["--model", os.environ.get("SDLC_RUN_MODEL") or config.orchestrator_model(cfg)]
        return cmd

    def active(self, req: str) -> Run | None:
        return next((r for r in self.runs.values() if r.req == req and r.status == "running"), None)

    def start(self, req: str) -> Run:
        with self.lock:
            if self.active(req):
                raise RuntimeError(f"Agents are already running for {req}")
            self.logs.mkdir(parents=True, exist_ok=True)
            run_id = f"{req}-{time.strftime('%Y%m%d-%H%M%S')}"
            log = self.logs / f"{run_id}.log"
            run = Run(run_id, req, self.command(req), str(log.relative_to(self.root)), events.now())
            f = open(log, "w")
            f.write(f"$ {shlex.join(run.command)}\n\n")
            f.flush()
            try:
                proc = subprocess.Popen(run.command, cwd=self.root, stdout=f, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True, env=run_env(run_id))
            except OSError as e:
                f.close()
                raise RuntimeError(f"Could not start Claude Code ({run.command[0]}): {e}") from e
            run.pid, run._proc = proc.pid, proc
            self.runs[run_id] = run
        events.emit(self.root, "run_started", req=req, detail=f"/sdlc-run {req} from dashboard", status="running",
                    run=run_id)
        threading.Thread(target=self._wait, args=(run, f), daemon=True).start()
        return run

    def _wait(self, run: Run, f) -> None:
        code = run._proc.wait()
        f.close()
        with self.lock:
            run.exit_code, run.ended = code, events.now()
            if run.status == "running":
                run.status = "finished" if code == 0 else "failed"
        run.cost_usd = reported_cost(self.root / run.log)
        if run.status == "failed":
            run.error = failure_reason(self.root / run.log)
        run.summary = final_words(self.root / run.log)
        events.emit(self.root, "run_finished", req=run.req, status=run.status, run=run.id,
                    detail=(run.error or f"exit {code}") + (f" · ${run.cost_usd:.2f}" if run.cost_usd else ""),
                    cost_usd=run.cost_usd)

    def stop(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if not run:
            raise KeyError(run_id)
        if run.status == "running" and run._proc:
            run.status = "stopped"
            try:
                os.killpg(run._proc.pid, signal.SIGTERM)  # the whole session: claude and its children
            except ProcessLookupError:
                pass
        return run

    def tail(self, run_id: str, max_bytes: int = 256 * 1024) -> str:
        """The run's log as readable lines (stream-json is turned into text; other lines pass through)."""
        run = self.runs.get(run_id)
        if not run:
            raise KeyError(run_id)
        path = self.root / run.log
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - max_bytes))
            raw = fh.read().decode(errors="replace")
        return "\n".join(filter(None, (readable(line) for line in raw.splitlines())))

    def list(self) -> list[dict[str, Any]]:
        return [r.public() for r in sorted(self.runs.values(), key=lambda r: r.started, reverse=True)]


def run_env(run_id: str) -> dict[str, str]:
    """The environment for a headless run. SDLC_RUN_AUTH=login drops ANTHROPIC_API_KEY so Claude Code uses the
    claude.ai login (for a shell that has an old or wrong key set)."""
    env = {**os.environ, "SDLC_DASHBOARD_RUN": run_id}
    if os.environ.get("SDLC_RUN_AUTH") == "login":
        env.pop("ANTHROPIC_API_KEY", None)
    return env


_HINTS = (
    ("invalid api key", "Claude Code rejected the API key in ANTHROPIC_API_KEY. Fix or remove that key in the shell "
                        "that started the dashboard, or restart it with `SDLC_RUN_AUTH=login sdlc dashboard` to use "
                        "your claude.ai login instead."),
    ("not logged in", "Claude Code is not signed in: use “Sign in again”, or run `claude` in a terminal and /login."),
    ("failed to authenticate", "Claude Code's sign-in has expired: use “Sign in again”, then run the agents again."),
    ("oauth", "Claude Code's sign-in has expired: use “Sign in again”, then run the agents again."),
    ("credit balance", "The Anthropic account has no credit left."),
    ("rate limit", "Claude Code hit a rate or usage limit; try again later."),
    ("unknown command", "Claude Code could not find /sdlc-run: the Agentic SDLC plugin did not load (check SDLC_PLUGIN_DIR)."),
)


def failure_reason(log: Path) -> str | None:
    """A short, actionable reason from the run's log (the CLI's error result, or the last stderr line)."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return None
    said = ""
    for line in reversed(lines):
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "result" and msg.get("is_error"):
            said = str(msg.get("result") or msg.get("subtype") or "")
            break
    if not said:
        said = next((ln for ln in reversed(lines) if ln.strip() and not ln.startswith("{")), "")
    low = said.lower()
    return next((hint for key, hint in _HINTS if key in low), said[:300] or None)


def final_words(log: Path) -> str | None:
    """The run's final result: what it did and where it stopped."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"type":"result"' in line.replace(" ", ""):
            try:
                text = str(json.loads(line).get("result") or "").strip()
            except json.JSONDecodeError:
                return None
            text = text.replace("**", "").replace("`", "")  # the card scrolls, so keep the whole message
            return text[:2000] + ("…" if len(text) > 2000 else "") or None
    return None


def reported_cost(log: Path) -> float | None:
    """The CLI's own total_cost_usd from the run's final `result` message, if it printed one."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"type":"result"' in line.replace(" ", ""):
            try:
                c = json.loads(line).get("total_cost_usd")
                return float(c) if isinstance(c, (int, float)) else None
            except json.JSONDecodeError:
                return None
    return None


def readable(line: str) -> str:
    """One stream-json message from `claude -p --output-format stream-json` as a short text line."""
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return line
    kind = msg.get("type")
    if kind == "system" and msg.get("subtype") == "init":
        return f"· session started ({msg.get('model', '')})"
    if kind == "assistant":
        out = []
        for block in msg.get("message", {}).get("content", []):
            if block.get("type") == "text" and block["text"].strip():
                out.append(block["text"].strip())
            elif block.get("type") == "tool_use":
                inp = block.get("input", {})
                arg = inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                out.append(f"→ {block['name']}: {str(arg)[:160]}")
        prefix = "  " if msg.get("parent_tool_use_id") else ""  # inside a subagent
        return "\n".join(prefix + o for o in out)
    if kind == "result":
        cost = msg.get("total_cost_usd")
        tail = f" · ${cost:.2f}" if isinstance(cost, (int, float)) else ""
        return f"\n✔ done in {round(msg.get('duration_ms', 0) / 1000)}s{tail}\n{msg.get('result', '')}"
    return ""


def _claude_env() -> dict[str, str]:
    env = {**os.environ}
    if os.environ.get("SDLC_RUN_AUTH") == "login":
        env.pop("ANTHROPIC_API_KEY", None)
    return env


def auth_status() -> dict[str, Any]:
    """Who Claude Code is signed in as (`claude auth status`), for the dashboard's sign-in help."""
    binary = os.environ.get("SDLC_CLAUDE_BIN", "claude")
    try:
        r = subprocess.run([binary, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                           env=_claude_env(), stdin=subprocess.DEVNULL)
        d = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"logged_in": False, "detail": "Claude Code did not answer"}
    return {"logged_in": bool(d.get("loggedIn")), "method": d.get("authMethod"), "email": d.get("email")}


_login: subprocess.Popen | None = None


def start_login() -> dict[str, Any]:
    """Start `claude auth login`: it opens the claude.ai sign-in page in the browser and finishes by itself."""
    global _login
    if _login and _login.poll() is None:
        return {"started": True, "already": True}
    binary = os.environ.get("SDLC_CLAUDE_BIN", "claude")
    try:
        _login = subprocess.Popen([binary, "auth", "login", "--claudeai"], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_claude_env(),
                                  start_new_session=True)
    except OSError as e:
        raise RuntimeError(f"Could not start Claude Code sign-in: {e}") from e
    return {"started": True}
