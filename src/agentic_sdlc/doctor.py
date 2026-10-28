"""`sdlc doctor`: is this repo set up the way Agentic SDLC and the company expect? One line per check, ✓ ⚠ ✗."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import __version__, config, generate, hosts


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail}


def run(root: Path) -> list[dict[str, str]]:
    out = [_check("agentic-sdlc", "ok", f"version {__version__}")]
    try:
        cfg = config.load(root)  # checks `requires` and the org's min_version too
    except config.ConfigError as e:
        return [*out, _check("config", "fail", str(e))]
    out.append(_check("config", "ok", "sdlc.config.yaml is valid"
                      + (f"; needs {cfg['requires']}" if cfg.get("requires") else "")))

    plugin = hosts.PLUGIN / ".claude-plugin" / "plugin.json"
    pv = json.loads(plugin.read_text()).get("version") if plugin.exists() else None
    out.append(_check("plugin", "ok" if pv == __version__ else "warn",
                      f"plugin {pv} matches the CLI" if pv == __version__ else
                      f"plugin {pv or 'not found'} vs CLI {__version__}: update the plugin (`/plugin`) or the CLI"))

    o = cfg.get("_org")
    if o:
        out.append(_check("company policy", "ok", f"{o['name'] or 'org'} ({o['path']})"
                          + (f"; applied: {'; '.join(o['notes'])}" if o["notes"] else "; nothing overridden")))
    git = (root / ".git").exists()
    out.append(_check("git", "ok" if git else "fail", "git repository" if git else "not a git repository: `git init`"))
    integ = cfg.get("integrations") or {}
    if integ.get("pull_requests"):
        remote = integ.get("remote", "origin")
        has = git and remote in subprocess.run(["git", "remote"], cwd=root, capture_output=True, text=True).stdout.split()
        out.append(_check("git remote", "ok" if has else "fail", f"`{remote}` set" if has else f"no `{remote}` remote"))
        gh = shutil.which("gh") and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
        out.append(_check("GitHub CLI", "ok" if gh else "fail", "logged in" if gh else "install gh and `gh auth login`"))

    comps = config.components(cfg)
    cmds = {f"{c['name'] + ': ' if c['name'] else ''}{k}": v for c in comps for k, v in c["commands"].items()}
    cmds.update({k: v for k, v in (cfg.get("stack", {}).get("commands") or {}).items() if k not in cmds})
    missing = sorted({v.split()[0] for v in cmds.values() if v.split() and not shutil.which(v.split()[0])
                      and not v.startswith((".", "/"))})
    out.append(_check("tools", "ok" if not missing else "warn",
                      f"{len(cmds)} commands configured" if not missing else f"not on PATH: {', '.join(missing)}"))
    for name in (o or {}).get("required_commands") or []:
        present = any(k.endswith(name) for k in cmds)
        out.append(_check(f"required check: {name}", "ok" if present else "fail",
                          "configured" if present else "required by company policy: add it to stack.commands"))

    differ = [rel for name, rel in generate.PROJECT_TEMPLATES.items()
              if (root / rel).exists() and (root / rel).read_text() != generate.template(name)]
    out.append(_check("templates", "ok", f"{len(differ)} customised by this repo" if differ else "all match the defaults"))

    if "copilot" in integ.get("hosts", ["claude"]):
        stale = []
        for src in sorted((hosts.PLUGIN / "agents").glob("*.md")):
            name, text = hosts.copilot_agent(src, cfg)
            p = root / ".github" / "agents" / f"{name}.agent.md"
            if not p.exists() or (hosts.MARKER in p.read_text() and p.read_text() != text):
                stale.append(name)
        out.append(_check("Copilot export", "ok" if not stale else "warn",
                          "up to date" if not stale else f"{len(stale)} agents out of date: `sdlc agents export`"))
    from .dashboard.server import STATIC
    out.append(_check("dashboard", "ok" if (STATIC / "index.html").exists() else "warn",
                      "UI built" if (STATIC / "index.html").exists() else "UI not built (API only)"))
    return out


def failed(results: list[dict[str, Any]]) -> bool:
    return any(r["status"] == "fail" for r in results)
