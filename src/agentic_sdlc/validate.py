"""Deterministic checks the engine runs itself, so they never depend on an agent following its instructions.

- `spec_lint`: the spec has every section of its type's template, no empty or untouched section, acceptance
  criteria with Given/When/Then and a priority, unique AC ids, and an `n/a` only with a reason.
- `plan_check`: the task plan covers every `must` AC and every SEC-n / OPS-n / TEST-n / REL-n id the design and
  plans define, and tasks that can run at the same time do not touch the same files.
- `next_version`: the semantic version from the git tags, the work type and whether the change is breaking.
"""

import re
import subprocess
from pathlib import Path
from typing import Any

from . import generate

COMMENT = re.compile(r"<!--.*?-->", re.S)
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.M)
AC_LINE = re.compile(r"^\s*-\s+\*\*(AC-\d+)\*\*\s*(?:\(([^)]*)\))?\s*(.*)$")
PRIORITIES = {"must", "should", "could"}
OPTIONAL = {"open questions", "resolved decisions", "source notes"}  # may legitimately be empty
# Lines that only repeat the template: a bullet with nothing after it, "label:" with nothing after the colon,
# an untouched choice list, a <placeholder>, a bold label on its own, an empty table row or separator.
PLACEHOLDER = [
    re.compile(r"^[-*]\s*$|^\d+\.\s*$"),
    re.compile(r"^[-*]\s+[^:]{1,80}:\s*$"),
    re.compile(r"^[-*]?\s*[\w /()-]*:\s*(S / M / L|low / medium / high, because|must / should / could|"
               r"P1 / P2 / P3|bug / not a bug.*|yes / no.*|critical / high / medium / low.*)\s*$", re.I),
    re.compile(r"<[a-z][^>]*>"),
    re.compile(r"^\*\*[^*]+\*\*:?\s*$"),
    re.compile(r"^\|[\s|:-]*\|$"),
    re.compile(r"^(As a <role>|Given|When|Then)\s*$"),
    re.compile(r"^[-*]\s+\*\*[A-Z]+-\d+\*\*\s*(\([^)]*\))?\s*$"),  # "- **BR-1**" with nothing after it
]


def _drop_empty_tables(lines: list[str]) -> list[str]:
    """A table counts only if a data row has content beyond its first cell (a template row like `| Risk |  |`)."""
    out, block = [], []

    def flush() -> None:
        rows = [r for r in block[2:] if r.strip("|").strip()]
        filled = [r for r in rows if any(c.strip() for c in r.strip().strip("|").split("|")[1:])]
        if filled:
            out.extend(block)
        block.clear()

    for ln in lines:
        if ln.startswith("|"):
            block.append(ln)
            continue
        if block:
            flush()
        out.append(ln)
    if block:
        flush()
    return out
NA = re.compile(r"\bn/a\b\s*[:.\-—]?\s*$", re.I)


def _template_headings(root: Path, template_name: str) -> list[str]:
    text = generate.render(template_name, root, req="REQ", title="T", links="")
    return [_norm(h) for _, h in HEADING.findall(text)][1:]  # skip the title


def _norm(h: str) -> str:
    return re.sub(r"^\d+\.\s*", "", h).strip().lower()


def _sections(text: str) -> list[tuple[int, str, str]]:
    """(level, heading, body) for every heading; body runs to the next heading of any level."""
    marks = list(HEADING.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((len(m.group(1)), m.group(2), text[m.end():end]))
    return out


def _meaningful(body: str, template_lines: set[str] = frozenset()) -> list[str]:
    """Lines someone actually wrote: not a placeholder pattern and not copied unchanged from the template."""
    lines = _drop_empty_tables([ln.strip() for ln in COMMENT.sub("", body).splitlines()])
    return [ln for ln in lines if ln and ln not in template_lines and not any(p.search(ln) for p in PLACEHOLDER)]


def _acs(text: str) -> list[dict[str, Any]]:
    acs, cur = [], None
    for line in COMMENT.sub("", text).splitlines():
        m = AC_LINE.match(line)
        if m:
            cur = {"id": m.group(1), "priority": (m.group(2) or "").strip().lower(), "title": m.group(3).strip(),
                   "steps": {}}
            acs.append(cur)
            continue
        step = re.match(r"^\s+-\s+(Given|When|Then)\b\s*(.*)$", line)
        if cur and step:
            cur["steps"][step.group(1)] = step.group(2).strip()
        elif cur and line.strip().startswith("- **"):
            cur = None
    return acs


def spec_lint(root: Path, req: str, wtype: str, template_name: str) -> dict[str, Any]:
    p = root / "specs" / f"{req}.md"
    if not p.exists():
        return {"ok": False, "errors": [f"specs/{req}.md does not exist"], "warnings": []}
    text = p.read_text()
    text = text.split("\n## Earlier notes", 1)[0]  # a retyped item keeps its old spec below this line
    errors, warnings = [], []
    template_text = COMMENT.sub("", generate.render(template_name, root, req=req, title="T", links=""))
    template_lines = {ln.strip() for ln in template_text.splitlines()
                      if ln.strip() and not ln.startswith("#") and not ln.strip().startswith("|")}
    have = {_norm(h) for _, h, _ in _sections(text)}
    for h in _template_headings(root, template_name):
        if h not in have:
            errors.append(f"missing section: {h}")
    sections = _sections(text)
    for i, (level, h, body) in enumerate(sections):
        name = _norm(h)
        if level == 1 or name in OPTIONAL or name == "acceptance criteria":
            continue
        has_children = i + 1 < len(sections) and sections[i + 1][0] > level
        if has_children:
            continue  # a parent heading's content is its subsections
        lines = _meaningful(body, template_lines)
        if not lines:
            errors.append(f"empty section: {h} (write it, or `n/a: <reason>`)")
        elif all(NA.search(ln) for ln in lines):
            errors.append(f"n/a without a reason: {h}")
    acs = _acs(text)
    if not acs:
        errors.append("no acceptance criteria (AC-1, AC-2 ...)")
    ids = [a["id"] for a in acs]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"duplicate {dup}")
    for a in acs:
        missing = [s for s in ("Given", "When", "Then") if not a["steps"].get(s)]
        if missing and not (a["title"] and not a["steps"]):  # a one-line AC without steps is fine for size S
            errors.append(f"{a['id']}: {' / '.join(missing)} is empty")
        if wtype in ("feature", "change") and a["priority"] not in PRIORITIES:
            errors.append(f"{a['id']}: set a priority (must, should or could)")
    if re.search(r"^Type:.*Priority:\s*must / should / could", text, re.M):
        warnings.append("header: choose the priority (must, should or could)")
    blocking = len(re.findall(r"^\s*-\s+\[ \]\s+\*\*Q\d+[^*]*blocking[^*]*\*\*", text, re.M | re.I))
    if blocking:
        warnings.append(f"{blocking} blocking open question(s) waiting for an answer")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "acceptance_criteria": len(acs),
            "must": sum(a["priority"] == "must" for a in acs)}


# ---- plan coverage ----

DESIGN_IDS = re.compile(r"\b((?:SEC|OPS|TEST|REL)-\d+)\b")


def defined_ids(root: Path, req: str) -> dict[str, str]:
    """SEC/OPS/TEST/REL ids named in the design and plan files, with the file that defines each."""
    sources = [f"docs/design/{req}/security.md", f"docs/design/{req}/operations.md",
               f"docs/plan/{req}/test-plan.md", f"docs/plan/{req}/release-plan.md"]
    out: dict[str, str] = {}
    for rel in sources:
        p = root / rel
        if p.exists():
            for line in COMMENT.sub("", p.read_text()).splitlines():
                if re.fullmatch(r"\s*[-*|]?\s*(?:SEC|OPS|TEST|REL)-\d+\s*:?\s*\|?\s*", line):
                    continue  # the template's empty "- SEC-1:" line is not a mitigation
                for i in DESIGN_IDS.findall(line):
                    out.setdefault(i, rel)
    return out


def _overlap(a: str, b: str) -> bool:
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def plan_check(root: Path, req: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    from .state import waves  # late: state imports this module

    spec = root / "specs" / f"{req}.md"
    acs = _acs(spec.read_text()) if spec.exists() else []
    covered = {x for t in tasks for x in t.get("acceptance", [])}
    missing_must = [a["id"] for a in acs if a["priority"] in ("must", "regression") and a["id"] not in covered]
    ids = defined_ids(root, req)
    missing_ids = [f"{i} ({src})" for i, src in sorted(ids.items()) if i not in covered]
    unknown = sorted(x for x in covered if re.fullmatch(r"AC-\d+", x) and x not in {a["id"] for a in acs})
    by_id = {t["id"]: t for t in tasks}
    overlaps = []
    for wave in waves(tasks):
        for i, a in enumerate(wave):
            for b in wave[i + 1:]:
                shared = [f for f in by_id[a].get("files", []) for g in by_id[b].get("files", []) if _overlap(f, g)]
                if shared:
                    overlaps.append(f"{a} and {b} both touch {shared[0]} and can run at the same time: "
                                    f"make one depend on the other, or merge them")
    return {"ok": not (missing_must or missing_ids or overlaps), "missing_must": missing_must,
            "missing_ids": missing_ids, "overlaps": overlaps, "unknown_refs": unknown}


# ---- versioning ----

SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _parse(v: str) -> tuple[int, int, int] | None:
    m = SEMVER.match(v.strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def latest_version(root: Path, exclude_req: str = "") -> tuple[int, int, int] | None:
    return latest_version_excluding(root, {exclude_req} if exclude_req else set())


def latest_version_excluding(root: Path, exclude: set[str]) -> tuple[int, int, int] | None:
    """The highest of the git tags and the versions other work items have already claimed for release."""
    found = []
    try:
        tags = subprocess.run(["git", "tag", "--list", "v*"], cwd=root, capture_output=True, text=True).stdout
        found += [v for t in tags.split() if (v := _parse(t))]
    except OSError:
        pass
    for notes in (root / "docs" / "release").glob("REQ-*/release-notes.md"):
        if notes.parent.name in exclude:
            continue
        m = re.match(r"Version:\s*(\S+)", notes.read_text())
        if m and (v := _parse(m.group(1))):
            found.append(v)
    return max(found) if found else None


def breaking(root: Path, req: str) -> bool:
    """Breaking if the release notes list breaking changes, or the spec says `breaking: yes`."""
    notes = root / "docs" / "release" / req / "release-notes.md"
    if notes.exists():
        m = re.search(r"^## Breaking changes[^\n]*\n(.*?)(?=^## |\Z)", COMMENT.sub("", notes.read_text()), re.M | re.S)
        lines = [ln.strip("- ").strip() for ln in (m.group(1).splitlines() if m else []) if ln.strip("- ").strip()]
        if lines and not all(ln.lower() in ("none", "n/a", "no") for ln in lines):
            return True
    spec = root / "specs" / f"{req}.md"
    return bool(spec.exists() and re.search(r"\bbreaking\W{1,3}(yes|true)\b", spec.read_text(), re.I))


def next_version(root: Path, req: str, wtype: str) -> dict[str, Any]:
    base = latest_version(root, exclude_req=req) or (0, 0, 0)
    major, minor, patch = base
    if breaking(root, req):
        bump = "minor" if major == 0 else "major"  # 0.x: breaking changes bump the minor version
        reason = "breaking change" + (" (0.x: minor)" if major == 0 else "")
    elif wtype in ("feature", "change"):
        bump, reason = "minor", f"a {wtype}"
    else:
        bump, reason = "patch", f"a {wtype}"
    new = ((major + 1, 0, 0) if bump == "major" else (major, minor + 1, 0) if bump == "minor"
           else (major, minor, patch + 1))
    if base == (0, 0, 0) and bump != "major":
        new = (0, 1, 0)  # first release
    return {"previous": "v%d.%d.%d" % base if base != (0, 0, 0) else None, "bump": bump, "reason": reason,
            "version": "v%d.%d.%d" % new}
