"""Keep a work item's pull request in step with its stages: draft until Release is approved, a `sdlc: <stage>`
label, and the commit statuses `Agentic SDLC / QA` and `Agentic SDLC / Release` that branch protection requires.

A gate's status is `success` only for the code that was approved: commits pushed after the approval set it back
to `pending`, unless they touch nothing but SDLC documents (release notes, reports, traceability).
Syncing is best effort: if GitHub cannot be reached, the work item carries on and the next sync catches up.
"""

from pathlib import Path
from typing import Any

from . import events, github, gitops, state

GATES = {"qa": "QA", "release": "Release"}


def _head(root: Path, req: str) -> str:
    return gitops.git(root, "rev-parse", f"sdlc/{req}", check=False).strip()


def code_changed(root: Path, since: str, head: str) -> list[str]:
    """Files changed between two commits that are not SDLC documents."""
    if not since or since == head:
        return []
    out = gitops.git(root, "diff", "--name-only", f"{since}..{head}", check=False)
    return [p for p in out.splitlines() if p and not gitops.managed(p)]


def record_approval_head(root: Path, req: str, stage: str) -> None:
    head = _head(root, req)
    if head:
        with state.edit(root, req) as item:
            item.setdefault("approved_heads", {})[stage] = head


def statuses(root: Path, item: dict[str, Any], head: str) -> dict[str, tuple[str, str]]:
    """context → (state, description) for the PR's head commit."""
    out = {}
    for stage, name in GATES.items():
        st = item["stages"].get(stage, {})
        status = st.get("status")
        if status == state.SKIPPED:
            out[name] = ("success", f"{name} stage is off for this item")
        elif status == state.APPROVED:
            approved = (item.get("approved_heads") or {}).get(stage, "")
            changed = code_changed(root, approved, head)
            by = (st.get("decision") or {}).get("by", "")
            out[name] = (("pending", f"code changed after the {name} approval ({len(changed)} files): approve again")
                         if changed else ("success", f"approved by {by}" if by else "approved"))
        elif stage == "qa" and (blockers := state.open_blocker_defects(item)):
            out[name] = ("failure", f"{len(blockers)} release blocker(s) open: {', '.join(blockers[:5])}")
        elif status == state.AWAITING:
            out[name] = ("pending", f"waiting for {', '.join(st.get('gate') or ['approval'])}")
        elif status in (state.IN_PROGRESS, state.BLOCKED):
            out[name] = ("pending", f"{name} in progress")
        else:
            out[name] = ("pending", f"{name} not started")
    return out


def sync(root: Path, cfg: dict[str, Any], req: str) -> dict[str, Any] | None:
    """Post statuses, set draft or ready, and the stage label. Returns what was set, or None without a PR."""
    integ = cfg.get("integrations") or {}
    item = state.load(root, req)
    pr = item.get("pr")
    if not (integ.get("pull_requests") and pr):
        return None
    head = _head(root, req)
    result: dict[str, Any] = {"number": pr["number"], "head": head[:7]}
    try:
        if integ.get("status_checks", True) and head:
            # GitHub can only hold a status for a commit it has: push the work-item branch first when the
            # newest commit (e.g. the QA reports) is still local
            remote = integ.get("remote") or "origin"
            if not gitops.git(root, "branch", "-r", "--contains", head, check=False).strip():
                gitops.push(root, f"sdlc/{req}", remote)
            sts = statuses(root, item, head)
            for name, (st, desc) in sts.items():
                github.set_status(root, head, name, st, desc, pr.get("url", ""))
            result["statuses"] = {k: v[0] for k, v in sts.items()}
        ready = all(v == "success" for v in (result.get("statuses") or {}).values()) and bool(result.get("statuses"))
        if integ.get("draft_prs", True):
            if pr.get("draft") != (not ready):
                github.set_draft(root, pr["number"], not ready)
            result["draft"] = not ready
        label = (f"{github.LABEL_PREFIX}ready to merge" if ready
                 else f"{github.LABEL_PREFIX}{item['current_stage'] or 'done'}")
        github.set_stage_label(root, pr["number"], label)
        result["label"] = label
    except (github.GitHubError, gitops.GitError, ValueError, KeyError) as e:  # ValueError: bad JSON from gh
        events.emit(root, "pr_sync_failed", req=req, status="failed", detail=str(e)[:200])
        result["error"] = str(e)[:200]
        return result
    with state.edit(root, req) as it:
        it["pr"] = {**(it.get("pr") or {}), "draft": result.get("draft", False), "statuses": result.get("statuses", {}),
                    "label": result.get("label", "")}
    events.emit(root, "pr_synced", req=req, detail=f"#{pr['number']}: " + ", ".join(
        f"{k} {v}" for k, v in (result.get("statuses") or {}).items()) + (" · draft" if result.get("draft") else " · ready"))
    return result
