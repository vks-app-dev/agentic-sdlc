"""Human-only actions, enforced by the engine whatever AI tool is running (Claude Code, Copilot, a script).

Approving or rejecting a gate, changing a work type, skipping a stage, deferring or accepting a defect,
unblocking a task and recording a production deploy are decisions people make. The engine runs them only when
a person is clearly present:
- in an interactive terminal, after the person types the work item id to confirm; or
- from the local dashboard, whose action endpoints require the page's per-launch token and a same-origin request.
Called any other way (an agent's shell tool, a pipe, a slash command), nothing changes: the engine files a
**pending request** in .sdlc/run/pending/ that a person confirms in the dashboard or with `sdlc confirm <id>`.

This stops unsanctioned agent actions in every host. It is not a defence against a program that deliberately
forges a terminal or the dashboard's headers on your own machine; company-grade identity (GitHub or SSO approvals)
is on the roadmap (docs/PARKED.md, P6).
"""

import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

from . import events

ACTIONS = {
    "approve": "approve the gate",
    "reject": "send the stage back",
    "retype": "change the work type",
    "skip": "skip the current stage",
    "defect-decision": "defer or accept a defect",
    "unblock": "unblock a task",
    "deployed": "record a deploy done by a person",
    "train-approve": "approve a release train",
    "train-deployed": "record a release train's deploy",
}


class NeedsHuman(Exception):
    """Raised (and reported to the caller) when a human-only action was filed as a pending request."""

    def __init__(self, request: dict[str, Any]):
        self.request = request
        super().__init__(
            f"{ACTIONS[request['action']].capitalize()} is a human decision. Nothing was changed: a request is "
            f"waiting for a person to confirm it in the dashboard, or in a terminal with `sdlc confirm {request['id']}`.")


def interactive() -> bool:
    """A person at a terminal. Agent shell tools, pipes and slash-command `!` lines have no TTY."""
    if os.environ.get("SDLC_DASHBOARD_RUN"):  # inside a headless agent run started from the dashboard
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def confirm_in_terminal(summary: str, req: str, assume_yes: bool = False) -> bool:
    print(summary)
    if assume_yes:
        return True
    try:
        typed = input(f"Type {req} to confirm: ").strip()
    except EOFError:
        return False
    return typed.upper() == req.upper()


def _dir(root: Path) -> Path:
    return root / ".sdlc" / "run" / "pending"


def request(root: Path, action: str, req: str, args: dict[str, Any], summary: str) -> dict[str, Any]:
    d = _dir(root)
    d.mkdir(parents=True, exist_ok=True)
    rid = f"{req}-{action}-{secrets.token_hex(3)}"
    entry = {"id": rid, "action": action, "req": req, "args": args, "summary": summary, "ts": events.now(),
             "from": ("dashboard agent run" if os.environ.get("SDLC_DASHBOARD_RUN")
                      else f"an AI tool via {os.environ['SDLC_VIA']}" if os.environ.get("SDLC_VIA")
                      else "non-interactive shell")}
    (d / f"{rid}.json").write_text(json.dumps(entry, indent=2))
    events.emit(root, "human_needed", req=req, status="pending", detail=f"confirm: {summary} ({rid})")
    return entry


def pending(root: Path) -> list[dict[str, Any]]:
    d = _dir(root)
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def take(root: Path, rid: str) -> dict[str, Any]:
    """Remove a pending request and return it (to run it, or to dismiss it)."""
    p = _dir(root) / f"{rid}.json"
    if not re.fullmatch(r"[\w.-]{1,80}", rid) or not p.exists():
        raise KeyError(rid)
    entry = json.loads(p.read_text())
    p.unlink()
    return entry


# ---- dashboard: proof that a person in the browser made the request ----

_TOKEN = secrets.token_urlsafe(24)


def dashboard_token() -> str:
    return _TOKEN


def browser_headers() -> dict[str, str]:
    """What the dashboard page sends with every action (used by tests; a browser adds Sec-Fetch-Site itself)."""
    return {"X-SDLC-Human": _TOKEN, "Sec-Fetch-Site": "same-origin"}


def from_browser(headers: dict[str, str]) -> bool:
    """The dashboard page's token plus a same-origin browser request (Sec-Fetch-Site is set by the browser)."""
    return (secrets.compare_digest(headers.get("x-sdlc-human", ""), _TOKEN)
            and headers.get("sec-fetch-site", "") == "same-origin")
