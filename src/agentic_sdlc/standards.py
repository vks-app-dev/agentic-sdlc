"""Standards and skills the agents follow: coding standards, testing, security, API, git, review, docs, UI.

Each standard is a Markdown file. By default it is Agentic SDLC's (or the company's, from org.yaml `templates:`),
copied to `docs/standards/<name>.md` where the team can edit it. A project can point a standard at its own file
instead, or add its own standards and Claude Code skills, in sdlc.config.yaml:

    standards:
      coding: CONTRIBUTING.md                       # use the team's file instead of the default
      testing: {path: docs/testing-guide.md}
      payments: {path: .claude/skills/payments/SKILL.md, title: Payments rules,
                 agents: [dev-coder, dev-code-reviewer]}  # an extra standard or skill for chosen agents

`sdlc next` lists, for each agent, the standards it must read; the CLAUDE.md section lists them all.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import generate


@dataclass(frozen=True)
class Standard:
    title: str
    about: str
    agents: tuple[str, ...]


BUILTIN: dict[str, Standard] = {
    "coding": Standard("Coding standards", "Style, structure, errors, data, dependencies, logging and configuration",
                       ("dev-coder", "dev-code-reviewer", "dev-pr-reviewer", "dev-pr-responder", "design-detail-designer")),
    "testing": Standard("Testing standards", "What to test, how, test levels and the quality bar",
                        ("dev-test-writer", "dev-coder", "dev-code-reviewer", "plan-test-planner",
                         "qa-functional-tester", "qa-exploratory-tester", "qa-performance-tester")),
    "security": Standard("Security standards", "Secrets, input, injection, access control, personal data, limits",
                         ("design-security-reviewer", "dev-coder", "dev-code-reviewer", "dev-pr-reviewer",
                          "qa-security-tester", "req-nfr-analyst")),
    "api": Standard("API guidelines", "Resources, methods, status codes, errors, naming, paging, compatibility",
                    ("design-data-api-designer", "dev-coder", "dev-code-reviewer", "dev-doc-writer")),
    "git": Standard("Git, commits and pull requests", "Branches, commit messages, PR descriptions, merging",
                    ("dev-coder", "dev-pr-responder")),
    "code-review": Standard("Code review checklist", "What reviewers check, in order, and how they report it",
                            ("dev-code-reviewer", "dev-pr-reviewer")),
    "documentation": Standard("Documentation standards", "README, API, architecture, configuration and runbooks",
                              ("dev-doc-writer", "design-architect", "design-ops-designer")),
    "ui": Standard("UI and accessibility standards", "States, accessibility, forms, responsive layout, wording",
                   ("design-ux-designer", "dev-coder", "qa-ui-tester")),
}

DEFAULT_DIR = "docs/standards"

# What a found file is probably about, from words in its path (first match wins).
_KINDS = (("code-review", r"review"), ("testing", r"test"), ("security", r"secur"), ("api", r"\bapi|rest|openapi"),
          ("git", r"commit|git|branch|pull.?request|contributing"), ("ui", r"\bui\b|ux|accessib|design.?system"),
          ("documentation", r"doc(s|umentation)?[-_ ]?(guide|standard|style)"), ("coding", r".*"))
_LOOKS_LIKE = re.compile(r"standard|guideline|convention|style|contributing|coding|best.?practice|checklist", re.I)


def _entry(value: Any) -> dict[str, Any]:
    return {"path": value} if isinstance(value, str) else dict(value or {})


def resolve(root: Path, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every standard with where it lives, whose it is and which agents follow it."""
    own = cfg.get("standards") or {}
    out = []
    for name, s in BUILTIN.items():
        e = _entry(own.get(name))
        path = e.get("path") or f"{DEFAULT_DIR}/{name}.md"
        p = root / path
        if e.get("path"):
            source = "yours"
        elif not p.exists():
            source = "missing"
        elif p.read_text() == generate.template(f"standards/{name}.md"):
            source = "company" if generate.ORG_TEMPLATES and (generate.ORG_TEMPLATES / "standards" / f"{name}.md").is_file() \
                else "default"
        else:
            source = "edited"
        out.append({"name": name, "title": e.get("title") or s.title, "about": s.about, "path": path,
                    "source": source, "exists": p.exists(), "agents": list(e.get("agents") or s.agents), "all": False,
                    "kind": "skill" if path.endswith("SKILL.md") else "standard"})
    for name, value in own.items():
        if name in BUILTIN:
            continue
        e = _entry(value)
        path = e.get("path", "")
        agents = e.get("agents")
        every = agents == "all" or not agents
        out.append({"name": name, "title": e.get("title") or name.replace("-", " ").capitalize(),
                    "about": e.get("about", "Added by this project"), "path": path, "source": "yours",
                    "exists": bool(path) and (root / path).exists(), "agents": [] if every else list(agents),
                    "all": every, "kind": "skill" if path.endswith("SKILL.md") else "standard"})
    return out


def for_agent(root: Path, cfg: dict[str, Any], agent: str) -> list[str]:
    """The standard files an agent reads before it starts (an added standard for "all" applies to every agent)."""
    return [s["path"] for s in resolve(root, cfg)
            if s["exists"] and (agent in s["agents"] or (s["all"] and s["name"] not in BUILTIN))]


def overridden(cfg: dict[str, Any]) -> set[str]:
    """Built-in standards the project points at its own file (their default copy is not written)."""
    return {n for n, v in (cfg.get("standards") or {}).items() if n in BUILTIN and _entry(v).get("path")}


def detect(root: Path) -> list[dict[str, str]]:
    """Files in the repo that look like the team's own standards or Claude Code skills, with a guessed kind."""
    found = []
    candidates = [*root.glob("*.md"), *root.glob("docs/**/*.md"), *root.glob(".github/*.md"),
                  *root.glob(".claude/skills/*/SKILL.md"), *root.glob(".cursor/rules/*"), *root.glob(".github/instructions/*.md")]
    skip = {"README.md", "CHANGELOG.md", "CLAUDE.md", "AGENTS.md", "LICENSE.md"}
    for p in sorted(set(candidates)):
        rel = str(p.relative_to(root))
        if p.name in skip or rel.startswith((f"{DEFAULT_DIR}/", "docs/design/", "docs/plan/", "docs/qa/", "docs/release/",
                                             "docs/adr/", "docs/product/", "docs/architecture/", "docs/dev/")):
            continue
        is_skill = p.name == "SKILL.md" or ".cursor/rules" in rel or ".github/instructions" in rel
        if not (is_skill or _LOOKS_LIKE.search(rel)):
            continue
        kind = next(k for k, pattern in _KINDS if re.search(pattern, rel.lower()))
        found.append({"path": rel, "kind": "skill" if is_skill else "standard", "suggested_for": kind})
    return found


def write_defaults(root: Path, cfg: dict[str, Any]) -> list[str]:
    """Copy the default of every built-in standard the project has not pointed elsewhere. Never overwrites."""
    done = []
    skip = overridden(cfg)
    for name in BUILTIN:
        p = root / DEFAULT_DIR / f"{name}.md"
        if name in skip or p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(generate.template(f"standards/{name}.md"))
        done.append(f"wrote {DEFAULT_DIR}/{name}.md")
    return done


def claude_lines(root: Path, cfg: dict[str, Any]) -> str:
    rows = []
    for s in resolve(root, cfg):
        if s["source"] == "missing":
            continue
        who = ", ".join(a.split("-", 1)[1].replace("-", " ") for a in s["agents"][:4]) or "every agent"
        more = f" +{len(s['agents']) - 4}" if len(s["agents"]) > 4 else ""
        rows.append(f"- {s['title']}: `{s['path']}` ({who}{more})")
    return "\n".join(rows) or "- (none)"


def import_templates(root: Path, folder: Path) -> list[str]:
    """Use a team's own templates: each file in `folder` whose name matches a known template (spec.md,
    design/README.md, standards/coding.md, ...) replaces the project copy. Returns what was taken."""
    done = []
    known = {**generate.PROJECT_TEMPLATES, **{f"standards/{n}.md": f"{DEFAULT_DIR}/{n}.md" for n in BUILTIN}}
    for name, rel in known.items():
        src = folder / name
        if src.is_file():
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text())
            done.append(f"took {rel} from {src}")
    return done


# Which agents fill or use each template (for the dashboard's Library).
TEMPLATE_AGENTS = {
    "spec.md": ["req-analyst", "req-nfr-analyst", "req-ambiguity-checker"],
    "change.md": ["req-change-impact-analyst", "req-analyst"], "bug.md": ["req-bug-triager"], "task.md": ["dev-coder"],
    "adr.md": ["design-architect"], "rca.md": [], "github/pull_request_template.md": ["dev-pr-reviewer"],
    "design/README.md": ["design-architect"], "design/data.md": ["design-data-api-designer"],
    "design/api.md": ["design-data-api-designer"], "design/ux.md": ["design-ux-designer"],
    "design/lld.md": ["design-detail-designer"], "design/security.md": ["design-security-reviewer"],
    "design/operations.md": ["design-ops-designer"], "plan/README.md": ["plan-task-planner"],
    "plan/test-plan.md": ["plan-test-planner"], "plan/release-plan.md": ["plan-release-planner"],
    **{f"qa/{n}.md": [f"qa-{n}-tester"] for n in ("functional", "security", "exploratory", "uat", "ui", "performance")},
    "release/readiness.md": ["rel-readiness-checker"], "release/release-notes.md": ["rel-notes-writer"],
    "release/rollback.md": ["rel-rollback-rehearser"], "release/verification.md": ["rel-deploy-verifier"],
}
TEMPLATE_TITLES = {
    "spec.md": "Feature spec", "change.md": "Change spec", "bug.md": "Bug report", "task.md": "Task spec",
    "adr.md": "Architecture decision (ADR)", "rca.md": "Root-cause analysis",
    "github/pull_request_template.md": "Pull request description", "design/README.md": "Design overview",
    "design/data.md": "Data model", "design/api.md": "API design", "design/ux.md": "UX design",
    "design/lld.md": "Low-level design", "design/security.md": "Threat model", "design/operations.md": "Operations design",
    "plan/README.md": "Task plan", "plan/test-plan.md": "Test plan", "plan/release-plan.md": "Release plan",
    "qa/functional.md": "Functional test report", "qa/security.md": "Security test report",
    "qa/exploratory.md": "Exploratory test report", "qa/uat.md": "UAT report", "qa/ui.md": "UI test report",
    "qa/performance.md": "Performance test report", "release/readiness.md": "Release readiness",
    "release/release-notes.md": "Release notes", "release/rollback.md": "Rollback rehearsal",
    "release/verification.md": "Staging verification",
}
_GROUPS = (("Specs", ("spec.md", "change.md", "bug.md", "task.md")), ("Design", ("design/", "adr.md")),
           ("Planning", ("plan/",)), ("QA", ("qa/",)), ("Release", ("release/",)), ("Other", ("",)))


def templates(root: Path) -> list[dict[str, Any]]:
    """Every document template: where the project's copy is, whose it is, and which agents fill it."""
    out = []
    for name, rel in generate.PROJECT_TEMPLATES.items():
        p = root / rel
        if not p.exists():
            source = "missing"
        elif p.read_text() == generate.template(name):
            source = "company" if generate.ORG_TEMPLATES and (generate.ORG_TEMPLATES / name).is_file() else "default"
        else:
            source = "edited"
        group = next(g for g, prefixes in _GROUPS if any(name.startswith(x) for x in prefixes))
        out.append({"name": name, "title": TEMPLATE_TITLES.get(name, name), "path": rel, "group": group,
                    "source": source, "exists": p.exists(), "agents": TEMPLATE_AGENTS.get(name, [])})
    order = [g for g, _ in _GROUPS]
    return sorted(out, key=lambda t: order.index(t["group"]))


# ---- changing them from the dashboard (a person only: the server checks the page token) ----

MAX_BYTES = 512 * 1024
UPLOAD_TYPES = (".md", ".markdown", ".txt")
HISTORY = Path(".sdlc") / "library-history"


class LibraryError(Exception):
    pass


def all_agents() -> list[str]:
    from .stages import STAGES, WORK_TYPES

    names = [st.agent for s in STAGES for st in s.steps]
    names += [st.agent for w in WORK_TYPES.values() for steps in w.steps.values() for st in steps]
    return list(dict.fromkeys(names))


def locate(root: Path, cfg: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    """One library entry: a standard (or skill) by name, or a template by its template name (e.g. design/api.md)."""
    rows = resolve(root, cfg) if kind == "standard" else templates(root) if kind == "template" else None
    if rows is None:
        raise LibraryError("kind must be standard or template")
    row = next((r for r in rows if r["name"] == name), None)
    if not row:
        raise LibraryError(f"No {kind} called {name}")
    default = (f"standards/{name}.md" if kind == "standard" and name in BUILTIN
               else name if kind == "template" else None)
    return {**row, "kind_of": kind, "default_name": default}


def _check(content: str, filename: str) -> None:
    if filename and not filename.lower().endswith(UPLOAD_TYPES):
        raise LibraryError(f"Upload a text file: {', '.join(UPLOAD_TYPES)}")
    if len(content.encode()) > MAX_BYTES:
        raise LibraryError("The file is larger than 512 KB")
    if "\x00" in content or not content.strip():
        raise LibraryError("The file is empty or not text")


def _backup(root: Path, rel: str) -> str | None:
    """Keep the previous version, so a replaced standard can be got back (.sdlc/library-history/)."""
    p = root / rel
    if not p.is_file():
        return None
    from . import events

    stamp = events.now()[:19].replace(":", "").replace("-", "")
    dst = root / HISTORY / f"{rel.replace('/', '__')}.{stamp}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(p.read_text())
    return str(dst.relative_to(root))


def replace(root: Path, cfg: dict[str, Any], kind: str, name: str, content: str, filename: str = "") -> dict[str, Any]:
    """Put a new version in place of the file the agents read for this entry."""
    entry = locate(root, cfg, kind, name)
    _check(content, filename)
    if not content.endswith("\n"):
        content += "\n"
    backup = _backup(root, entry["path"])
    p = root / entry["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {"path": entry["path"], "previous": backup}


def reset(root: Path, cfg: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    """Back to Agentic SDLC's (or the company's) default. A built-in standard that pointed at the team's own file
    points back at docs/standards/<name>.md; that file itself is left alone."""
    entry = locate(root, cfg, kind, name)
    if not entry["default_name"]:
        raise LibraryError(f"{name} was added by this project and has no default; remove it instead")
    path = entry["path"]
    if kind == "standard" and entry["source"] == "yours":
        _set_config(root, name, None)
        path = f"{DEFAULT_DIR}/{name}.md"
    backup = _backup(root, path)
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(generate.template(entry["default_name"]))
    return {"path": path, "previous": backup}


def similar(root: Path, cfg: dict[str, Any], title: str, content: str) -> dict[str, str] | None:
    """An existing standard this upload looks like, so the person replaces it instead of adding a duplicate."""
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    words = lambda s: set(norm(s).split()) - {"standard", "standards", "guide", "guidelines", "rules", "our", "the", "and"}  # noqa: E731
    body = content.strip()
    for s in resolve(root, cfg):
        p = root / s["path"]
        if s["exists"] and body and p.read_text().strip() == body:
            return {"name": s["name"], "title": s["title"], "reason": f"it is word for word the same as {s['title']}"}
        default = s["name"] in BUILTIN and generate.template(f"standards/{s['name']}.md").strip()
        if default and body == default:
            return {"name": s["name"], "title": s["title"], "reason": f"it is Agentic SDLC's default {s['title']}"}
    for s in resolve(root, cfg):
        if title and (norm(title) == norm(s["title"]) or (words(title) and words(title) <= words(s["title"] + " " + s["name"]))):
            return {"name": s["name"], "title": s["title"], "reason": f"you already have {s['title']}"}
    return None


def add(root: Path, name: str, title: str, agents: list[str] | str, content: str, skill: bool, filename: str = "") -> dict[str, Any]:
    """A new standard or Claude Code skill for the chosen agents, or "all" for every agent (a choice is required)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,39}", name):
        raise LibraryError("Name it with lowercase letters, digits and dashes, e.g. payments-rules")
    from . import config

    if name in BUILTIN or name in (config.load(root).get("standards") or {}):
        raise LibraryError(f"{name} already exists: replace it instead")
    if agents != "all":
        if not agents:
            raise LibraryError("Choose the agents that follow it, or All agents")
        unknown = sorted(set(agents) - set(all_agents()))
        if unknown:
            raise LibraryError(f"Unknown agents: {', '.join(unknown)}")
    _check(content, filename)
    path = f".claude/skills/{name}/SKILL.md" if skill else f"{DEFAULT_DIR}/{name}.md"
    if skill and not content.lstrip().startswith("---"):  # Claude Code skills start with frontmatter
        content = f"---\nname: {name}\ndescription: {title}\n---\n\n{content}"
    p = root / path
    if p.exists():
        raise LibraryError(f"{path} already exists")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content if content.endswith("\n") else content + "\n")
    _set_config(root, name, {"path": path, "title": title.strip() or name, "agents": agents})
    return {"path": path}


def remove(root: Path, name: str) -> dict[str, Any]:
    """Stop using a standard the project added (its file is kept)."""
    if name in BUILTIN:
        raise LibraryError("Built-in standards cannot be removed; reset or replace them")
    _set_config(root, name, None)
    return {"removed": name}


def _set_config(root: Path, name: str, value: Any) -> None:
    import yaml

    from . import config

    raw = yaml.safe_load((root / config.CONFIG_FILE).read_text()) or {}
    std = raw.setdefault("standards", {})
    if value is None:
        std.pop(name, None)
    else:
        std[name] = value
    if not std:
        raw.pop("standards", None)
    config.validate(raw)
    config.write(root, raw)
