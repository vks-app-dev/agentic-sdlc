"""Environments, deployments, smoke checks and the release pack of a work item.

**Agents deploy only to dev and staging. Production is deployed by a person.** The engine never runs a production
(or any `human_only`) deploy command: `sdlc deploy REQ production` refuses and prints the command for a person;
the person then records it with `sdlc deployed REQ production`. The guard hook blocks models from both commands
and from the configured production commands (written to .sdlc/run/guard.json), and headless runs are never
allowed them.

Config:
    environments:
      dev:        {deploy: "...", url: "http://localhost:8000", smoke: ["curl -fsS http://localhost:8000/health"]}
      staging:    {deploy: "...", rollback: "...", url: "...", smoke: [...]}
      production: {deploy: "...", rollback: "...", url: "...", smoke: [...]}   # always human-only
"""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import events, generate, gitops, state

AGENT_ENVS = ("dev", "staging")
PROD_NAMES = {"production", "prod", "live"}
PACK = ("readiness", "release-notes", "rollback", "verification")
VERDICT_FILES = ("readiness", "rollback", "verification")  # must say PASS (or N/A) before the Release gate


class ReleaseError(Exception):
    pass


def environments(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return cfg.get("environments") or {}


def human_only(cfg: dict[str, Any], env: str) -> bool:
    """Production-like environments are human-only, whatever the config says."""
    e = environments(cfg).get(env) or {}
    return env.lower() in PROD_NAMES or bool(e.get("human_only")) or env not in AGENT_ENVS and not e.get("agent_ok")


def guard_rules(cfg: dict[str, Any]) -> dict[str, Any]:
    """What the PreToolUse hook must block, in a form it can read without YAML."""
    cmds = []
    for name, e in environments(cfg).items():
        if human_only(cfg, name):
            cmds += [e[k] for k in ("deploy", "rollback") if e.get(k)]
    return {"human_only_envs": sorted({n for n in environments(cfg) if human_only(cfg, n)} | PROD_NAMES),
            "commands": cmds}


def write_guard_file(root: Path, cfg: dict[str, Any]) -> None:
    p = root / ".sdlc" / "run" / "guard.json"
    data = json.dumps(guard_rules(cfg), indent=2)
    if not p.exists() or p.read_text() != data:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)


def _env(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    e = environments(cfg).get(name)
    if e is None:
        known = ", ".join(environments(cfg)) or "none"
        raise ReleaseError(f"No environment '{name}' in sdlc.config.yaml (known: {known})")
    return e


def _run(root: Path, cmd: str, timeout: int = 900) -> dict[str, Any]:
    import tempfile

    start = time.monotonic()
    # Output goes to a file, not a pipe: a deploy script that starts a server in the background (staging) can leave
    # a helper process holding the pipe open, and reading a pipe waits for every holder. A file only waits for the
    # command itself to exit.
    with tempfile.TemporaryFile(mode="w+") as f:
        try:
            r = subprocess.run(cmd, shell=True, cwd=root, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               text=True, timeout=timeout)
            code = r.returncode
        except subprocess.TimeoutExpired:
            code = 124
        f.seek(0)
        out = f.read() + (f"\ntimed out after {timeout}s" if code == 124 else "")
    return {"command": cmd, "exit_code": code, "seconds": round(time.monotonic() - start, 1),
            "output": "\n".join(out.strip().splitlines()[-20:])}


def _head(root: Path) -> str:
    try:
        return gitops.git(root, "rev-parse", "--short", "HEAD").strip()
    except gitops.GitError:
        return ""


def deploy(root: Path, req: str, env: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Run the deploy command of an agent environment (dev, staging) and record it."""
    e = _env(cfg, env)
    if human_only(cfg, env):
        cmd = e.get("deploy") or "<your production deploy>"
        raise ReleaseError(f"{env} is deployed by a person, never by Agentic SDLC or an agent. When you are ready: "
                           f"merge the PR, run `{cmd}` yourself, then `sdlc deployed {req} {env}` and "
                           f"`sdlc smoke {req} {env}`.")
    if not e.get("deploy"):
        raise ReleaseError(f"environments.{env}.deploy is not set in sdlc.config.yaml")
    r = _run(root, e["deploy"])
    record = {"env": env, "commit": _head(root), "ts": events.now(), "by": "agentic-sdlc", "ok": r["exit_code"] == 0}
    _record(root, req, "deployments", record, f"deployed to {env}" if record["ok"] else f"deploy to {env} FAILED")
    return {**record, **r}


def deployed(root: Path, req: str, env: str, by: str, version: str = "", note: str = "") -> dict[str, Any]:
    """A person deployed (typically production) themselves and records it."""
    record = {"env": env, "commit": _head(root), "version": version, "ts": events.now(), "by": by, "ok": True,
              "note": note}
    _record(root, req, "deployments", record, f"{by} deployed to {env}" + (f" ({version})" if version else ""))
    return record


def smoke(root: Path, req: str, env: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Run the environment's smoke commands (read-only checks) and write docs/release/<REQ>/smoke-<env>.md."""
    e = _env(cfg, env)
    cmds = e.get("smoke") or ([f"curl -fsS {e['url'].rstrip('/')}/health"] if e.get("url") else [])
    if not cmds:
        raise ReleaseError(f"environments.{env} has no smoke commands or url")
    results = [_run(root, c, 120) for c in cmds]
    ok = all(r["exit_code"] == 0 for r in results)
    lines = [f"# Smoke check on {env}: {req}", "", f"{'Passed' if ok else 'FAILED'} · commit `{_head(root)}` · "
             f"{events.now()}. Generated by `sdlc smoke {req} {env}`.", "",
             "| Command | Result | Time |", "| --- | --- | --- |"]
    lines += [f"| `{r['command']}` | {'✅ passed' if r['exit_code'] == 0 else '❌ exit ' + str(r['exit_code'])} | "
              f"{r['seconds']}s |" for r in results]
    for r in results:
        if r["exit_code"] != 0 and r["output"]:
            lines += ["", f"## `{r['command']}`", "", "```", r["output"], "```"]
    if not ok and e.get("rollback"):
        lines += ["", "## Roll back", "", f"If this is not a transient problem, roll back: `{e['rollback']}`"
                  + (" (a person runs this; production is human-only)." if human_only(cfg, env) else ".")]
    p = root / "docs" / "release" / req / f"smoke-{env}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    _record(root, req, "smoke", {"env": env, "ok": ok, "ts": events.now(), "commit": _head(root)},
            f"smoke on {env} {'passed' if ok else 'FAILED'}")
    return {"env": env, "passed": ok, "report": str(p.relative_to(root)), "results": results}


def _record(root: Path, req: str, key: str, entry: dict[str, Any], what: str) -> None:
    with state.edit(root, req) as item:
        item.setdefault(key, []).append(entry)
        item["history"].append({"ts": entry["ts"], "what": key.rstrip("s") if key != "smoke" else "smoke",
                                "env": entry["env"], "ok": entry["ok"]})
        stage = item["current_stage"]
    events.emit(root, "deployed" if key == "deployments" else "smoke", req=req, stage=stage,
                status="passed" if entry["ok"] else "failed", detail=what)


def last(item: dict[str, Any], key: str, env: str) -> dict[str, Any] | None:
    return next((x for x in reversed(item.get(key, [])) if x["env"] == env), None)


# ---- release pack ----

def pack(root: Path, req: str) -> Path:
    return root / "docs" / "release" / req


def new_pack(root: Path, req: str, title: str) -> list[Path]:
    out = []
    for name in PACK:
        p = pack(root, req) / f"{name}.md"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(generate.render(f"release/{name}.md", root, req=req, title=title))
        out.append(p)
    return out


def version(root: Path, req: str) -> str:
    p = pack(root, req) / "release-notes.md"
    m = re.match(r"Version:\s*(v?\d+\.\d+\.\d+\S*)", p.read_text()) if p.exists() else None
    return m.group(1) if m else ""


def record_approval(root: Path, req: str, by: str, comment: str) -> list[str]:
    p = pack(root, req) / "release-notes.md"
    if not p.exists():
        return []
    when = events.now()[:16].replace("T", " ")
    line = f"Release approved by {by} on {when}" + (f": {comment}" if comment else "") + "."
    text = re.sub(r"(## Approval\n)(<!--.*?-->\n)?", lambda m: f"{m.group(1)}{line}\n", p.read_text(), count=1,
                  flags=re.S)
    p.write_text(text)
    return [str(p.relative_to(root))]


def go_live(cfg: dict[str, Any], item: dict[str, Any], root: Path) -> dict[str, Any] | None:
    """After the Release approval: is the item ready to go live (a person merges, deploys and records it), or live?
    None before Release is approved."""
    rel = item["stages"].get("release") or {}
    if rel.get("status") != state.APPROVED:
        return None
    prod = [d for d in item.get("deployments", []) if d.get("ok") and (d["env"].lower() in PROD_NAMES or human_only(cfg, d["env"]))]
    env = next((n for n in environments(cfg) if n.lower() in PROD_NAMES), "production")
    e = environments(cfg).get(env) or {}
    out: dict[str, Any] = {"env": env, "version": version(root, item["id"]), "pr": item.get("pr"),
                           "deploy_command": e.get("deploy"), "url": e.get("url"),
                           "approved": (rel.get("decision") or {}).get("at")}
    if prod:
        last = prod[-1]
        smoke = [s for s in item.get("smoke", []) if s["env"] == last["env"] and s["ts"] >= last["ts"]]
        return {**out, "state": "live", "deployed": last, "smoke": smoke[-1] if smoke else None}
    return {**out, "state": "ready"}
