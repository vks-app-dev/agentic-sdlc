"""The design pack of a work item: docs/design/<REQ>/README.md plus one file per workstream that applies.

README.md holds the context, options, recommendation, high-level design, rollout, traceability and the decision.
Each workstream file is written by its own agent, so agents in the same group can run in parallel without
editing the same file. Decisions that outlive the work item become numbered ADRs in docs/adr/.
Items designed before design packs existed keep their single docs/adr/ADR-<REQ>.md.
"""

import re
from datetime import date
from pathlib import Path

from . import events, generate

WORKSTREAMS = {
    "data": "Database and data design",
    "api": "API and integration design",
    "ux": "UI/UX design",
    "operations": "Deployment, observability, scaling and recovery",
    "lld": "Detailed technical design and test design",
    "security": "Threat model and security design",
}


def pack_dir(root: Path, req: str) -> Path:
    return root / "docs" / "design" / req


def legacy_adr(req: str) -> str:
    return f"docs/adr/ADR-{req}.md"


def files(req: str) -> list[str]:
    """Every design file a work item can have, README first (callers keep the ones that exist)."""
    base = f"docs/design/{req}"
    return [f"{base}/README.md", *(f"{base}/{w}.md" for w in WORKSTREAMS), legacy_adr(req)]


def new_pack(root: Path, req: str, title: str) -> Path:
    p = pack_dir(root, req) / "README.md"
    if not p.exists() and not (root / legacy_adr(req)).exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(generate.render("design/README.md", root, req=req, title=title))
    return p if p.exists() else root / legacy_adr(req)


def workstream(root: Path, req: str, title: str, name: str) -> Path:
    if name not in WORKSTREAMS:
        raise ValueError(f"Unknown workstream '{name}'. Known: {', '.join(WORKSTREAMS)}")
    p = pack_dir(root, req) / f"{name}.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(generate.render(f"design/{name}.md", root, req=req, title=title))
    return p


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "decision"


def new_adr(root: Path, req: str, title: str) -> Path:
    """A numbered, product-wide ADR (docs/adr/0001-use-sqlite.md), linked to the work item that raised it."""
    d = root / "docs" / "adr"
    d.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in d.glob("*.md") if (m := re.match(r"(\d{4})-", p.name))]
    number = f"{(max(nums) + 1) if nums else 1:04d}"
    p = d / f"{number}-{_slug(title)}.md"
    p.write_text(generate.render("adr.md", root, number=number, title=title, req=req, date=date.today().isoformat()))
    return p


def record_approval(root: Path, req: str, by: str, comment: str) -> list[str]:
    """The design gate was approved: write the decision into the pack and accept the item's proposed ADRs."""
    when = events.now()[:16].replace("T", " ")
    line = f"Approved by {by} on {when}" + (f": {comment}" if comment else "") + "."
    changed = []
    readme = pack_dir(root, req) / "README.md"
    if readme.exists():
        text = readme.read_text().replace("Status: proposed", "Status: approved", 1)
        text = re.sub(r"(## Decision\n)(<!--.*?-->\n)?", lambda m: f"{m.group(1)}{line}\n", text, count=1, flags=re.S)
        readme.write_text(text)
        changed.append(str(readme.relative_to(root)))
    for adr in sorted((root / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")):
        text = adr.read_text()
        if f"Work item: {req}" in text and "Status: proposed" in text:
            adr.write_text(text.replace("Status: proposed", f"Status: accepted ({when}, {by})", 1))
            changed.append(str(adr.relative_to(root)))
    return changed
