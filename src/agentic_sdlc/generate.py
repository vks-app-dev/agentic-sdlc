"""Generate the per-repo setup from sdlc.config.yaml: CLAUDE.md section, templates, folders, permissions."""

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

START, END = "<!-- agentic-sdlc:start", "<!-- agentic-sdlc:end -->"
# Before the rename to Agentic SDLC the section was marked sdlc-pilot: still found and replaced
OLD_START, OLD_END = "<!-- sdlc-pilot:start", "<!-- sdlc-pilot:end -->"
# Templates a project can change: `sdlc init` copies the default once, then the project's copy is used.
PROJECT_TEMPLATES = {"spec.md": "specs/TEMPLATE.md", "change.md": "specs/TEMPLATE-change.md",
                     "bug.md": "specs/TEMPLATE-bug.md", "task.md": "specs/TEMPLATE-task.md",
                     "adr.md": "docs/adr/TEMPLATE.md", "rca.md": "docs/rca/TEMPLATE.md",
                     "github/pull_request_template.md": ".github/pull_request_template.md",
                     **{f"release/{n}.md": f"docs/release/TEMPLATE/{n}.md"
                        for n in ("readiness", "release-notes", "rollback", "verification")},
                     **{f"design/{n}.md": f"docs/design/TEMPLATE/{n}.md"
                        for n in ("README", "data", "api", "ux", "lld", "security", "operations")},
                     **{f"plan/{n}.md": f"docs/plan/TEMPLATE/{n}.md" for n in ("README", "test-plan", "release-plan")},
                     **{f"qa/{n}.md": f"docs/qa/TEMPLATE/{n}.md"
                        for n in ("functional", "security", "exploratory", "uat", "ui", "performance")}}
# Product-wide documents every work item reads and adds to (stakeholders, personas, data, integrations, obligations).
PRODUCT_DOCS = {**{f"product/{n}.md": f"docs/product/{n}.md" for n in ("overview", "data", "integrations", "compliance")},
                "architecture/overview.md": "docs/architecture/overview.md",
                "architecture/data-model.md": "docs/architecture/data-model.md"}


ORG_TEMPLATES: Path | None = None  # set from org.yaml `templates:` when the config is loaded


def template(name: str) -> str:
    """The default template: the company's (org.yaml `templates:`) if it has this one, else Agentic SDLC's."""
    if ORG_TEMPLATES and (ORG_TEMPLATES / name).is_file():
        return (ORG_TEMPLATES / name).read_text()
    return resources.files("agentic_sdlc").joinpath("templates", name).read_text()


def render(template_name: str, root: Path | None = None, **values: str) -> str:
    """Fill a template. The project's own copy wins over the packaged default when it exists."""
    own = root / PROJECT_TEMPLATES[template_name] if root and template_name in PROJECT_TEMPLATES else None
    text = own.read_text() if own and own.is_file() else template(template_name)
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


def claude_section(cfg: dict[str, Any], root: Path | None = None) -> str:
    proj, stack = cfg["project"], cfg["stack"]
    cmds = stack.get("commands") or {}
    cmd_lines = "\n".join(f"- {k}: `{v}`" for k, v in cmds.items())
    for name, comp in (cfg.get("components") or {}).items():
        path = comp.get("path", name)
        cmd_lines += f"\n- **{name}** ({comp.get('kind', 'other')}, run in `{path}/`): " + (
            ", ".join(f"{k} `{v}`" for k, v in (comp.get("commands") or {}).items()) or "no commands set")
    cmd_lines = cmd_lines.strip() or "- (none set: add them to `stack.commands`)"
    stack_desc = ", ".join([*stack.get("languages", []), *(filter(None, [stack.get("framework")]))]) or "not set"
    compliance = f", compliance: {', '.join(proj['compliance'])}" if proj.get("compliance") else ""
    existing = ""
    if proj.get("type") == "existing":
        existing = ("- Existing code: small PRs only. If the area has no tests, write characterization tests "
                    "first. Match the patterns already in the file you are changing.")
    from . import standards

    std = standards.claude_lines(root, cfg) if root else "- see `sdlc standards`"
    return render("claude_section.md", name=proj["name"], type=proj["type"], compliance=compliance,
                  stack=stack_desc, commands=cmd_lines, existing_rules=existing, standards=std).rstrip() + "\n"


def write_claude_md(root: Path, cfg: dict[str, Any]) -> str:
    return write_rules_file(root, cfg, "CLAUDE.md")


def write_rules_file(root: Path, cfg: dict[str, Any], name: str) -> str:
    """The Agentic SDLC section in CLAUDE.md (Claude Code) or AGENTS.md (Copilot and other tools), between markers."""
    path = root / name
    section = claude_section(cfg, root)
    if not path.exists():
        path.write_text(f"# {cfg['project']['name']}\n\n{section}")
        return "created"
    text = path.read_text()
    pattern = re.compile(rf"(?:{re.escape(START)}|{re.escape(OLD_START)}).*?(?:{re.escape(END)}|{re.escape(OLD_END)})\n?", re.S)
    if pattern.search(text):
        path.write_text(pattern.sub(lambda _: section, text))
        return "updated section"
    path.write_text(text.rstrip() + "\n\n" + section)
    return "appended section"


def write_settings(root: Path, cfg: dict[str, Any]) -> str:
    """Allow the sdlc CLI and the stack's own commands without prompting."""
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    settings = json.loads(path.read_text()) if path.exists() else {}
    allow = settings.setdefault("permissions", {}).setdefault("allow", [])
    wanted = ["Bash(sdlc:*)", "Bash(git worktree:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)"]
    for cmd in (cfg["stack"].get("commands") or {}).values():
        wanted.append(f"Bash({cmd}:*)")
    added = [w for w in wanted if w not in allow]
    allow.extend(added)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return f"{len(added)} permissions added"


def ensure_templates(root: Path, cfg: dict[str, Any] | None = None) -> list[str]:
    """Copy any default template or product doc the project does not have yet. Never overwrites the project's copy."""
    done = []
    for name, rel in {**PROJECT_TEMPLATES, **PRODUCT_DOCS}.items():
        p = root / rel
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            text = template(name)
            if name == "product/compliance.md":
                text = _seed_compliance(text, ((cfg or {}).get("project") or {}).get("compliance") or [])
            p.write_text(text)
            done.append(f"wrote {rel}")
    from . import standards

    return done + standards.write_defaults(root, cfg or {})


def _seed_compliance(text: str, frameworks: list[str]) -> str:
    """One obligations row per framework named in the config, for people to complete."""
    if not frameworks:
        return text
    rows = "\n".join(f"| {f} | (to complete) |  |  |" for f in frameworks)
    return text.replace("| --- | --- | --- | --- |\n|  |  |  |  |", f"| --- | --- | --- | --- |\n{rows}", 1)


def scaffold(root: Path, cfg: dict[str, Any]) -> list[str]:
    for d in (".sdlc/state", ".sdlc/plans", ".sdlc/reviews", "specs", "docs/adr", "docs/design", "docs/plan", "docs/rca"):
        (root / d).mkdir(parents=True, exist_ok=True)
    done = ensure_templates(root, cfg)
    gi = root / ".sdlc" / ".gitignore"
    if not gi.exists():
        # Specs, ADRs, plans and reviews are committed; runtime files the engine rewrites all the time are not.
        gi.write_text("events.jsonl\nstate/\nrun/\nlibrary-history/\n")
    done.append(f"CLAUDE.md: {write_claude_md(root, cfg)}")
    done.append(f".claude/settings.json: {write_settings(root, cfg)}")
    return done


def new_spec(root: Path, req: str, title: str, template_name: str = "spec.md", links: str = "") -> Path:
    p = root / "specs" / f"{req}.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render(template_name, root, req=req, title=title, links=links or "none"))
    return p


PLAN_FILES = ("README", "test-plan", "release-plan")


def new_plan_pack(root: Path, req: str, title: str) -> list[Path]:
    """docs/plan/<REQ>/: README (tasks, dependencies), test-plan and release-plan, each filled by its own agent."""
    out = []
    for n in PLAN_FILES:
        p = root / "docs" / "plan" / req / f"{n}.md"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(render(f"plan/{n}.md", root, req=req, title=title))
        out.append(p)
    return out


def retemplate_spec(root: Path, req: str, title: str, template_name: str, links: str, old_type: str) -> Path:
    """After a retype: the new type's template on top, the old spec kept below it for the agents to use."""
    p = root / "specs" / f"{req}.md"
    old = p.read_text() if p.exists() else ""
    p.write_text(render(template_name, root, req=req, title=title, links=links or "none")
                 + (f"\n## Earlier notes (written when this was a {old_type})\n\n{old}" if old.strip() else ""))
    return p

