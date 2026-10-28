"""Quality and security checks for a work item's branch, run by the engine (not reported by an agent).

`sdlc check REQ-001` runs, on the current checkout:
- every check command configured in `stack.commands` (lint, typecheck, test, build, audit, sast, secrets);
- a built-in secret scan of the files the branch changed, so there is always secret detection even when no
  scanner is installed.
Results go to docs/dev/<REQ>/checks.md (the evidence) and the work item's state, and Development cannot be
completed until the latest run passed after the last merged task.
"""

import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import events, gitops

# Configured commands that count as checks, in the order they run. Every configured one is required.
CHECK_COMMANDS = ("lint", "typecheck", "test", "coverage", "build", "e2e", "audit", "sast", "secrets")
COVERAGE_FILES = ("coverage.json", "coverage/coverage-summary.json", "coverage/lcov.info", "lcov.info",
                  "coverage.xml")
TIMEOUT = 900

SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Stripe key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}\b")),
    ("hard-coded credential", re.compile(
        r"""(?i)(?<![a-z])(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)(?![a-z])\s*[:=]\s*["']([^"'\s]{12,})["']""")),
]
GENERIC = "hard-coded credential"  # noisy: only for high-entropy values outside test files
WORDS = re.compile(r"[a-z]+(?:[-_. ][a-z0-9]+)*")  # "my-private-note", "secret-value-123": test sentinels, not keys
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|fixtures?)/|(^|/)test_[^/]+$|_test\.|\.(test|spec)\.")
PLACEHOLDER = re.compile(r"(?i)example|changeme|placeholder|dummy|fake|test|xxx|\$\{|<[^>]+>|\*{3}")
SKIP_SUFFIXES = {".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff", ".woff2", ".svg"}


def coverage_percent(folder: Path, output: str = "") -> float | None:
    """Line coverage from the report the test tool wrote: coverage.py JSON or XML, Istanbul/Jest/Vitest summary,
    lcov; else the last percentage the command printed on a TOTAL / All files line."""
    import json as _json

    for rel in COVERAGE_FILES:
        p = folder / rel
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        try:
            if rel.endswith("coverage.json"):
                return float(_json.loads(text)["totals"]["percent_covered"])
            if rel.endswith("coverage-summary.json"):
                return float(_json.loads(text)["total"]["lines"]["pct"])
            if rel.endswith("coverage.xml"):
                m = re.search(r'line-rate="([\d.]+)"', text)
                if m:
                    return round(float(m.group(1)) * 100, 2)
            if rel.endswith("lcov.info"):
                found = sum(int(x) for x in re.findall(r"^LF:(\d+)", text, re.M))
                hit = sum(int(x) for x in re.findall(r"^LH:(\d+)", text, re.M))
                return round(100 * hit / found, 2) if found else None
        except (KeyError, ValueError, _json.JSONDecodeError):
            continue
    m = re.findall(r"^(?:TOTAL|All files)\b.*?(\d+(?:\.\d+)?)%", output, re.M)
    return float(m[-1]) if m else None


def _base(root: Path) -> str | None:
    """The commit the work-item branch started from: where it meets main/master."""
    for b in ("main", "master"):
        r = subprocess.run(["git", "merge-base", "HEAD", b], cwd=root, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def changed_files(root: Path) -> list[str]:
    try:
        base = _base(root)
        args = ["diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD"] if base else ["ls-files"]
        return [p for p in gitops.git(root, *args).splitlines() if p]
    except gitops.GitError:
        return []


def commit_messages(root: Path, req: str) -> list[dict[str, str]] | None:
    """Commits on the work-item branch since it left main (None when there is no base to compare with)."""
    base = _base(root)
    if not base:
        return None
    try:
        out = gitops.git(root, "log", "--format=%h%x09%s", f"{base}..HEAD")
    except gitops.GitError:
        return None
    return [dict(zip(("sha", "subject"), line.split("\t", 1))) for line in out.splitlines() if "\t" in line]


def scan_secrets(root: Path, files: list[str]) -> list[dict[str, Any]]:
    findings = []
    for rel in files:
        p = root / rel
        if p.suffix.lower() in SKIP_SUFFIXES or not p.is_file() or p.stat().st_size > 1_000_000:
            continue
        try:
            lines = p.read_text(errors="strict").splitlines()
        except (UnicodeDecodeError, OSError):
            continue  # binary
        is_test = bool(TEST_PATH.search(rel))
        for n, line in enumerate(lines, 1):
            for kind, pattern in SECRET_PATTERNS:
                m = pattern.search(line)
                if not m or PLACEHOLDER.search(value := m.group(m.lastindex or 0)):
                    continue
                if kind == GENERIC and (is_test or WORDS.fullmatch(value)):
                    continue
                findings.append({"file": rel, "line": n, "kind": kind})
                break
    return findings


def _run(root: Path, name: str, cmd: str) -> dict[str, Any]:
    start = time.monotonic()
    try:
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=TIMEOUT)
        code, out = r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        code, out = 124, f"timed out after {TIMEOUT}s"
    tail = "\n".join(out.strip().splitlines()[-15:])
    status = "passed" if code == 0 else ("missing tool" if code == 127 else "failed")
    return {"name": name, "command": cmd, "status": status, "exit_code": code,
            "seconds": round(time.monotonic() - start, 1), "output": tail}


def run(root: Path, req: str, cfg: dict[str, Any]) -> dict[str, Any]:
    from .config import components  # every component's checks, each in its own folder

    comps = components(cfg)
    results, configured = [], set()
    minimum = cfg.get("stages", {}).get("development", {}).get("coverage_min")
    for c in comps:
        for name in CHECK_COMMANDS:
            if c["commands"].get(name):
                configured.add(name)
                label = f"{c['name']}: {name}" if c["name"] else name
                r = _run(root / c["path"], label, c["commands"][name])
                if name == "coverage":
                    pct = coverage_percent(root / c["path"], r["output"])
                    r["coverage"] = pct
                    if pct is None:
                        r.update(status="failed", output=(r["output"] + "\nno coverage report found").strip())
                    elif minimum is not None and pct < float(minimum) and r["status"] == "passed":
                        r.update(status="failed", output=f"line coverage {pct}% is below the minimum {minimum}%")
                    r["command"] += f"  → {pct}%" if pct is not None else ""
                results.append(r)
    for name in ("audit", "sast", "secrets"):  # repo-wide scanners may sit in stack.commands
        cmd = (cfg.get("stack", {}).get("commands") or {}).get(name)
        if cmd and comps[0]["name"] and name not in configured:
            configured.add(name)
            results.append(_run(root, name, cmd))
    for name in (cfg.get("_org") or {}).get("required_commands") or []:  # company policy: these must be set up
        if name not in configured and not (cfg.get("stack", {}).get("commands") or {}).get(name):
            results.append({"name": name, "command": "(not configured)", "seconds": 0, "status": "failed",
                            "exit_code": 1, "output": f"{cfg['_org'].get('name') or 'Company'} policy requires a "
                                                      f"`{name}` check: add it to stack.commands"})
    msgs = commit_messages(root, req)
    if msgs is not None:
        bad = [m for m in msgs if not (m["subject"].startswith(f"{req}:") or m["subject"].startswith(f"{req}/"))]
        results.append({"name": "commit messages (built-in)", "command": f"{len(msgs)} commits start with {req}",
                        "seconds": 0, "status": "failed" if bad else "passed", "exit_code": 1 if bad else 0,
                        "output": "\n".join(f"{m['sha']} {m['subject']}" for m in bad[:20])})
    files = changed_files(root)
    found = scan_secrets(root, files)
    results.append({"name": "secret scan (built-in)", "command": f"{len(files)} changed files", "seconds": 0,
                    "status": "failed" if found else "passed", "exit_code": 1 if found else 0,
                    "output": "\n".join(f"{f['file']}:{f['line']} {f['kind']}" for f in found)})
    head = ""
    try:
        head = gitops.git(root, "rev-parse", "--short", "HEAD").strip()
    except gitops.GitError:
        pass
    covs = [r["coverage"] for r in results if r.get("coverage") is not None]
    report = {"ts": events.now(), "head": head, "passed": all(r["status"] == "passed" for r in results),
              "coverage": min(covs) if covs else None,
              "results": results, "not_configured": [c for c in CHECK_COMMANDS if c not in configured]}
    write_report(root, req, report)
    return report


def write_report(root: Path, req: str, report: dict[str, Any]) -> Path:
    icon = {"passed": "✅", "failed": "❌", "missing tool": "⚠️"}
    lines = [f"# Checks: {req}", "",
             f"{'Passed' if report['passed'] else 'FAILED'} · commit `{report['head'] or '-'}` · {report['ts']}. "
             f"Generated by `sdlc check {req}`; do not edit by hand.", "",
             "| Check | Command | Result | Time |", "| --- | --- | --- | --- |"]
    for r in report["results"]:
        lines.append(f"| {r['name']} | `{r['command']}` | {icon[r['status']]} {r['status']} | {r['seconds']}s |")
    if report["not_configured"]:
        lines += ["", "Not configured (add to `stack.commands` in sdlc.config.yaml to run them): "
                  + ", ".join(report["not_configured"]) + "."]
    for r in report["results"]:
        if r["status"] != "passed" and r["output"]:
            lines += ["", f"## {r['name']}: {r['status']}", "", "```", r["output"], "```"]
    p = root / "docs" / "dev" / req / "checks.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    return p
