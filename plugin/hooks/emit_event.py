#!/usr/bin/env python3
"""Claude Code hook: turn agent activity into lines in .sdlc/events.jsonl.

Stdlib only and always exits 0: a hook must never slow down or break the session.
The line format matches agentic_sdlc/events.py and schema/event.schema.json.

How an event gets its REQ / task:
  1. SDLC_REQ / SDLC_TASK environment variables (set when the engine launches headless agents)
  2. For subagents: the orchestrator puts "REQ-001/T2" in the Agent tool's description. PreToolUse
     on the Agent tool queues that, SubagentStart claims it by agent type and remembers it by
     agent_id, so every later tool call inside that subagent is tagged.
  3. Otherwise the work item last started with `sdlc start` (.sdlc/run/active.json).

Usage: when a subagent stops, its transcript (…/<session>/subagents/agent-<id>.jsonl) is read and one `usage` event
per model is written; when the main session stops, only the transcript lines added since the last read are
counted. Messages are counted once by message id (a transcript repeats a message for each streamed block).
"""

import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REF = re.compile(r"\b(REQ-\d+)(?:/(T[\w-]+))?")
MAX_DETAIL = 200


def find_root(cwd: str) -> Path | None:
    here = Path(cwd).resolve()
    for d in (here, *here.parents):
        if (d / "sdlc.config.yaml").exists():
            return d
    return None


def short(text: object, n: int = MAX_DETAIL) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def bare(agent_type: str | None) -> str:
    """'agentic-sdlc:dev-coder' -> 'dev-coder' so queue entries match however the type was written."""
    return (agent_type or "").split(":")[-1]


class RunDir:
    """Small JSON files under .sdlc/run/ that link subagents to work items."""

    def __init__(self, root: Path):
        self.dir = root / ".sdlc" / "run"
        (self.dir / "agents").mkdir(parents=True, exist_ok=True)

    def _locked(self, name: str, update):
        path = self.dir / name
        with open(path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            raw = f.read()
            data = json.loads(raw) if raw.strip() else []
            data, result = update(data)
            f.seek(0)
            f.truncate()
            json.dump(data, f)
        return result

    def queue(self, agent: str, ref: dict) -> None:
        self._locked("pending.json", lambda q: (q + [{"agent": bare(agent), **ref}], None))

    def claim(self, agent: str) -> dict:
        def take(q):
            for i, entry in enumerate(q):
                if entry["agent"] == bare(agent):
                    return q[:i] + q[i + 1:], {k: v for k, v in entry.items() if k != "agent"}
            return q, {}
        return self._locked("pending.json", take)

    def remember(self, agent_id: str, ref: dict) -> None:
        (self.dir / "agents" / f"{agent_id}.json").write_text(json.dumps(ref))

    def recall(self, agent_id: str | None) -> dict:
        if not agent_id:
            return {}
        p = self.dir / "agents" / f"{agent_id}.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def active(self) -> dict:
        p = self.dir / "active.json"
        return json.loads(p.read_text()) if p.exists() else {}


USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def read_usage(path: Path, start: int = 0, seen: set | None = None) -> tuple[dict, int]:
    """Tokens per model in a transcript from byte `start`, each message once. Returns (per_model, end_offset)."""
    per_model: dict = {}
    seen = seen if seen is not None else set()
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    end = start + data.rfind(b"\n") + 1 if b"\n" in data else start  # only whole lines
    for line in data[: end - start].splitlines():
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        msg = d.get("message") if d.get("type") == "assistant" else None
        if not isinstance(msg, dict) or not msg.get("usage") or msg.get("id") in seen:
            continue
        seen.add(msg.get("id"))
        u, m = msg["usage"], msg.get("model") or "unknown"
        tot = per_model.setdefault(m, {k: 0 for k in USAGE_KEYS} | {"messages": 0})
        for k in USAGE_KEYS:
            tot[k] += int(u.get(k) or 0)
        tot["messages"] += 1
    return per_model, end


def subagent_transcript(hook: dict) -> Path | None:
    p = hook.get("agent_transcript_path")
    if p:
        return Path(p)
    main, aid = hook.get("transcript_path"), hook.get("agent_id")
    if not (main and aid):
        return None
    cand = Path(main).with_suffix("") / "subagents" / f"agent-{aid}.jsonl"
    return cand if cand.exists() else None


def main_usage_delta(run: "RunDir", transcript: Path) -> dict:
    """Usage added to the main transcript since the last Stop (offset and seen ids kept in .sdlc/run)."""
    state_file = run.dir / "usage-offsets.json"
    with open(state_file, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        st = json.loads(raw) if raw.strip() else {}
        key = str(transcript)
        entry = st.get(key, {"offset": 0, "seen": []})
        seen = set(entry["seen"])
        per_model, end = read_usage(transcript, entry["offset"], seen)
        st[key] = {"offset": end, "seen": list(seen)[-2000:]}
        f.seek(0)
        f.truncate()
        f.write(json.dumps(st))
    return per_model


def usage_events(base: dict, per_model: dict) -> list:
    return [{**base, "event": "usage", "model": m, **{k: v for k, v in t.items()}} for m, t in per_model.items()
            if any(t[k] for k in USAGE_KEYS)]


def ref_from(text: str) -> dict:
    m = REF.search(text or "")
    if not m:
        return {}
    return {k: v for k, v in (("req", m.group(1)), ("task", m.group(2))) if v}


def tool_detail(tool: str, inp: dict, resp: object, root: Path) -> str:
    if tool == "Bash":
        out = ""
        if isinstance(resp, dict):
            code = resp.get("exit_code", resp.get("returncode"))
            out = f" → exit {code}" if code is not None else ""
            if resp.get("interrupted"):
                out = " → interrupted"
        return short(inp.get("command", ""), 150) + out
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    try:
        return str(Path(path).relative_to(root)) if path else ""
    except ValueError:  # outside the repo, e.g. a coder's worktree
        return path


def build(hook: dict, run: RunDir, root: Path) -> dict | None:
    name = hook.get("hook_event_name")
    agent_id, agent_type = hook.get("agent_id"), hook.get("agent_type")
    env = {k: v for k, v in (("req", os.environ.get("SDLC_REQ")), ("task", os.environ.get("SDLC_TASK"))) if v}
    ev: dict = {"session": hook.get("session_id", "")[:8]}

    if name == "PreToolUse":  # main session dispatching a subagent
        inp = hook.get("tool_input") or {}
        ref = env or ref_from(inp.get("description", "")) or ref_from(inp.get("prompt", ""))
        sub = inp.get("subagent_type") or "general-purpose"
        if ref:
            run.queue(sub, ref)
        ev.update(event="agent_dispatch", agent=sub, status="queued", detail=short(inp.get("description")), **ref)
        return ev

    if name == "SubagentStart":
        ref = env or run.claim(agent_type) or run.active()
        if agent_id:
            run.remember(agent_id, ref)
        ev.update(event="agent_start", agent=agent_type, agent_id=agent_id, status="running", **ref)
        return ev

    inp = hook.get("tool_input") or {}
    ref = env or run.recall(agent_id) or ref_from(inp.get("command", "")) or run.active()
    if name == "PostToolUse":
        tool = hook.get("tool_name", "")
        ev.update(event="tool_use", tool=tool, agent=agent_type or "orchestrator", agent_id=agent_id,
                  status="running", detail=tool_detail(tool, inp, hook.get("tool_response"), root),
                  **ref)
        return ev
    stage = run.active().get("stage") if ref.get("req") and ref.get("req") == run.active().get("req") else None
    if name == "SubagentStop":
        ev.update(event="agent_stop", agent=agent_type, agent_id=agent_id, status="finished",
                  detail=short(hook.get("last_assistant_message")), **ref)
        path = subagent_transcript(hook)
        usage = usage_events({"session": ev["session"], "agent": bare(agent_type), "agent_id": agent_id,
                              "stage": stage, **ref}, read_usage(path)[0]) if path and path.exists() else []
        return [ev, *usage]
    if name == "Stop":
        ev.update(event="session_idle", agent="orchestrator", status="idle", **ref)
        path = Path(hook["transcript_path"]) if hook.get("transcript_path") else None
        usage = usage_events({"session": ev["session"], "agent": "orchestrator", "stage": stage, **ref},
                             main_usage_delta(run, path)) if path and path.exists() and ref.get("req") else []
        return [ev, *usage]
    return None


def main() -> None:
    try:
        hook = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    root = find_root(hook.get("cwd") or os.getcwd())
    if root is None:  # not an Agentic SDLC repo: stay silent
        return
    out = build(hook, RunDir(root), root)
    if not out:
        return
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = "".join(json.dumps({"ts": ts, "source": "hook", **{k: v for k, v in ev.items() if v not in (None, "")}},
                               ensure_ascii=False) + "\n" for ev in (out if isinstance(out, list) else [out]))
    path = root / ".sdlc" / "events.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, lines.encode())  # one write: concurrent hooks never interleave lines
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the session; leave a trace for debugging
        try:
            sys.stderr.write(f"agentic-sdlc hook error: {e}\n")
        except Exception:
            pass
    sys.exit(0)
