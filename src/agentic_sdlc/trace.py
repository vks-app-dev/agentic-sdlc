"""Requirements traceability: acceptance criterion → task → tests, plus the decision log.

Built from files, not by an agent, so it is always true to the repo:
- ACs from `## Acceptance criteria` lines like `- **AC-3** (must) title` in specs/<REQ>.md
- tasks from the work item's plan (`acceptance` lists)
- tests from test files that say "REQ-001 AC-3", or that mention the REQ and say "AC-3"
- decisions from the spec's `## Resolved decisions` and the gate history
Written to docs/rtm/<REQ>.md by `sdlc trace`.
"""

import re
import subprocess
from pathlib import Path
from typing import Any

from . import design, state

AC_LINE = re.compile(r"^\s*-\s+\*\*(AC-\d+)\*\*\s*(?:\(([^)]*)\))?\s*(.*)$")
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".sdlc"}


def acceptance_criteria(spec: str) -> list[dict[str, str]]:
    out, seen = [], set()
    for line in spec.splitlines():
        m = AC_LINE.match(line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append({"id": m.group(1), "priority": (m.group(2) or "").strip(), "title": m.group(3).strip()})
    return out


def _section(spec: str, heading: str) -> list[str]:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", spec, re.M | re.S)
    if not m:
        return []
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    return [line.strip()[2:].strip() for line in body.splitlines() if line.strip().startswith("- ") and line.strip() != "-"]


QUESTION = re.compile(r"^\s*-\s+\[[ xX]\]\s+\*\*(Q\d+)[^*]*\*\*\s*(.*)$")
ANSWER = re.compile(r"^\s*\*\*Answer:\*\*\s*(.*)$")


def answered_questions(spec: str) -> list[str]:
    """Open questions that already have an answer, as decision-log lines: "Q3: question → answer"."""
    out, current = [], None
    for line in spec.splitlines():
        if m := QUESTION.match(line):
            current = (m.group(1), m.group(2).strip())
        elif current and (a := ANSWER.match(line)):
            if a.group(1).strip():
                q = current[1] if len(current[1]) <= 90 else current[1][:87] + "..."
                out.append(f"{current[0]}: {q} → {a.group(1).strip()}")
            current = None
    return out


def _test_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.splitlines()
        rels = [Path(p) for p in out]
    except (OSError, subprocess.CalledProcessError):  # not a git repo
        rels = [p.relative_to(root) for p in root.rglob("*") if p.is_file()
                and not SKIP_DIRS & set(p.relative_to(root).parts)]
    return [root / p for p in rels if TEST_FILE.search(p.as_posix())]


def tests_by_ac(root: Path, req: str) -> dict[str, list[str]]:
    """AC id → test files that cover it. A file counts for an unprefixed "AC-3" only if it is about this REQ."""
    explicit = re.compile(rf"{re.escape(req)}\W{{1,3}}(AC-\d+)")
    bare = re.compile(r"\bAC-\d+\b")
    slug = req.lower().replace("-", "")  # tests/acceptance/test_req001_acceptance.py
    found: dict[str, set[str]] = {}
    for f in _test_files(root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        ids = set(explicit.findall(text))
        if req in text or slug in rel.lower().replace("-", "").replace("_", ""):
            ids |= set(bare.findall(text))
        for ac in ids:
            found.setdefault(ac, set()).add(rel)
    return {k: sorted(v) for k, v in found.items()}


def design_by_ac(root: Path, req: str) -> dict[str, list[str]]:
    """AC id → design files of this item that mention it (README traceability table, api.md endpoints, ...)."""
    found: dict[str, list[str]] = {}
    for rel in design.files(req):
        p = root / rel
        if p.is_file():
            text = re.sub(r"^\|\s*AC-\d+\s*(\|\s*)+$", "", p.read_text(errors="replace"), flags=re.M)  # empty template rows
            for ac in sorted(set(re.findall(r"\bAC-\d+\b", text))):
                found.setdefault(ac, []).append(rel)
    return found


def build(root: Path, req: str) -> dict[str, Any]:
    item = state.load(root, req)
    spec_path = root / "specs" / f"{req}.md"
    spec = spec_path.read_text() if spec_path.exists() else ""
    tests = tests_by_ac(root, req)
    designed = design_by_ac(root, req)
    rows = []
    for ac in acceptance_criteria(spec):
        tasks = [t["id"] for t in item["tasks"] if ac["id"] in t.get("acceptance", [])]
        if not tasks and len(item["tasks"]) == 1:
            tasks = [item["tasks"][0]["id"]]  # a single-task item covers every AC
        files = tests.get(ac["id"], [])
        rows.append({**ac, "design": designed.get(ac["id"], []), "tasks": tasks, "tests": files, "covered": bool(files)})
    gates = [h for h in item["history"] if h["what"] in ("gate_approved", "gate_rejected", "retyped", "stage_skipped")]
    return {"req": req, "title": item["title"], "type": state.item_type(item), "rows": rows,
            "uncovered": [r["id"] for r in rows if not r["covered"]],
            "uncovered_must": [r["id"] for r in rows if not r["covered"] and "must" in r["priority"]],
            "decisions": _section(spec, "Resolved decisions") + answered_questions(spec), "gates": gates}


def render(t: dict[str, Any]) -> str:
    covered = sum(r["covered"] for r in t["rows"])
    lines = [f"# Traceability: {t['req']} {t['title']}", "",
             f"Type: {t['type']} · {covered}/{len(t['rows'])} acceptance criteria have tests. "
             f"Generated by `sdlc trace {t['req']}`; do not edit by hand.", "",
             "## Acceptance criteria → design → tasks → tests", "",
             "| AC | Priority | Criterion | Design | Tasks | Tests |", "| --- | --- | --- | --- | --- | --- |"]
    for r in t["rows"]:
        tests = "<br>".join(f"`{x}`" for x in r["tests"]) or "**none**"
        where = ", ".join(Path(d).name if "/design/" in d else d for d in r["design"]) or "-"
        lines.append(f"| {r['id']} | {r['priority'] or '-'} | {r['title'].replace('|', '/') or '-'} | {where} | "
                     f"{', '.join(r['tasks']) or '-'} | {tests} |")
    if not t["rows"]:
        lines.append("| - | - | No acceptance criteria found in the spec | - | - | - |")
    lines += ["", "## Decision log", ""]
    lines += [f"- {d}" for d in t["decisions"]] or ["- No resolved decisions recorded in the spec."]
    lines += ["", "## Gate history", ""]
    for g in t["gates"]:
        who = f" by {g['by']}" if g.get("by") else ""
        what = g["what"].replace("_", " ")
        extra = g.get("comment") or g.get("reason") or ""
        lines.append(f"- {g['ts'][:16].replace('T', ' ')} · {g.get('stage', '')} · {what}{who}"
                     + (f": {extra}" if extra else ""))
    if not t["gates"]:
        lines.append("- No gate decisions yet.")
    return "\n".join(lines) + "\n"


def write(root: Path, req: str) -> tuple[Path, dict[str, Any]]:
    t = build(root, req)
    path = root / "docs" / "rtm" / f"{req}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(t))
    return path, t
