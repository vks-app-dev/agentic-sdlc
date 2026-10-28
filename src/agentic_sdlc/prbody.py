"""The body of a work item's pull request, from the repo's PR template and the work item's evidence.

The template is project-owned (`.github/pull_request_template.md`). Agentic SDLC's own template has {placeholders}
that are filled here. If the repo has its own template (no agentic-sdlc marker), that template is kept as is, so
the team's PR format is followed, and Agentic SDLC's details are added below it.
"""

import re
from pathlib import Path
from typing import Any

from . import generate, handover, state, trace

MARKER = "agentic-sdlc:pr-template"
OLD_MARKER = "sdlc-pilot:pr-template"  # templates written before the rename


def _section(text: str, heading: str) -> str:
    """First paragraph under a markdown heading, without HTML comments."""
    m = re.search(rf"^#+\s+(?:\d+\.\s+)?{re.escape(heading)}\s*$(.*?)(?=^#+\s|\Z)", text, re.M | re.S | re.I)
    if not m:
        return ""
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip()
    para = body.split("\n\n")[0].strip()
    return "" if para in ("-", "") else para


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text() if p.exists() else ""


def values(root: Path, req: str, cfg: dict[str, Any], blob: str = "") -> dict[str, str]:
    """`blob` is the URL prefix for files on the PR branch (e.g. https://github.com/o/r/blob/sdlc/REQ-001/):
    relative links do not resolve in a PR description."""
    item = state.load(root, req)
    url = lambda rel: f"{blob}{rel}"  # noqa: E731
    spec = _read(root, f"specs/{req}.md")
    summary = (_section(spec, "Problem") or _section(spec, "Why change it") or _section(spec, "Report")
               or _section(spec, "What and why") or item["title"])
    facts = handover.build(root, req, cfg)
    ho = _read(root, f"docs/dev/{req}/handover.md")
    t = trace.build(root, req)
    c = item.get("checks")
    reviews = [handover_review(root, req, x["id"]) for x in item["tasks"]]
    plan = _read(root, f"docs/plan/{req}/release-plan.md")
    design = _read(root, f"docs/design/{req}/README.md")
    rollback = (re.search(r"^- Rollback:\s*(.+)$", plan, re.M) or re.search(r"^- Rollback:\s*(.+)$", design, re.M))
    risk = re.sub(r"<!--.*?-->", "", rollback.group(1)).strip() if rollback else ""
    if not risk:
        risk = (f"See the [release plan]({url(f'docs/plan/{req}/release-plan.md')})." if plan
                else "Not recorded: this item has no release plan. Revert the merge commit to roll back.")
    docs = [(name, rel) for name, rel in (
        ("spec", f"specs/{req}.md"), ("design", f"docs/design/{req}/README.md"), ("design", f"docs/adr/ADR-{req}.md"),
        ("plan", f"docs/plan/{req}/README.md"), ("handover", f"docs/dev/{req}/handover.md"),
        ("QA report", f"docs/qa/{req}/summary.md")) if (root / rel).exists()]
    links = ", ".join(f"{x['rel'].replace('_', ' ')} {x['req']}" for x in item.get("links", []))
    return {
        "req": req, "title": item["title"], "type": state.item_type(item),
        "documents": " · ".join(f"[{name}]({url(rel)})" for name, rel in docs), "links": f"· {links}" if links else "",
        # placeholders of earlier template versions, so a repo's older copy still fills in
        **{k: url(dict(docs).get(k, rel)) for k, rel in (("spec", f"specs/{req}.md"), ("design", f"docs/design/{req}/README.md"),
                                                         ("plan", f"docs/plan/{req}/README.md"),
                                                         ("handover", f"docs/dev/{req}/handover.md"))},
        "summary": summary,
        "changes": _section(facts, "Changes") or "-",
        "how_to_test": "\n\n".join(x for x in (_section(ho, "How to verify"), _section(facts, "How to run")) if x) or "-",
        "checks": (f"passed at `{c['head']}`" if c and c["passed"] else "not passed" if c else "not run")
                  + f" ([report]({url(f'docs/dev/{req}/checks.md')}))",
        "traceability": f"{sum(r['covered'] for r in t['rows'])}/{len(t['rows'])} acceptance criteria have tests "
                        f"([matrix]({url(f'docs/rtm/{req}.md')}))",
        "reviews": f"{sum(v == 'APPROVE' for v in reviews)}/{len(reviews)} approved by dev-code-reviewer",
        "tasks": "\n".join(f"- {x['id']}: {_short(x['title'])}" for x in item["tasks"]) or "-",
        "risk": risk or "See the release plan.",
        "limitations": _section(ho, "Known limitations") or "none recorded",
    }


def _short(text: str, n: int = 100) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def handover_review(root: Path, req: str, task: str) -> str:
    p = root / ".sdlc" / "reviews" / f"{req}-{task}.md"
    return p.read_text().splitlines()[0].removeprefix("Verdict:").strip() if p.exists() else "-"


def body(root: Path, req: str, cfg: dict[str, Any], blob: str = "") -> str:
    template = _read(root, ".github/pull_request_template.md") or generate.template("github/pull_request_template.md")
    v = values(root, req, cfg, blob)
    if MARKER in template or OLD_MARKER in template:
        text = re.sub(r"<!--.*?-->\n?", "", template, count=1, flags=re.S)
        for k, val in v.items():
            text = text.replace("{" + k + "}", val)
        return text.strip() + "\n"
    own = generate.template("github/pull_request_template.md")  # the team's template first, our evidence after
    details = re.sub(r"<!--.*?-->\n?", "", own, count=1, flags=re.S)
    details = details[details.index("**Type:**"):details.index("### Reviewer checklist")]
    for k, val in v.items():
        details = details.replace("{" + k + "}", val)
    return f"{template.strip()}\n\n---\n<details><summary>Agentic SDLC: {req} evidence</summary>\n\n{details.strip()}\n\n</details>\n"


def title(root: Path, req: str) -> str:
    item = state.load(root, req)
    return f"{req}: {item['title']}"[:240]

