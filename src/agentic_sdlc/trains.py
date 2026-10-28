"""Release trains: several work items released together, with one version, one set of notes and one go/no-go.

    sdlc train new 2026.11 REQ-004 REQ-007 REQ-009     # .sdlc/trains/2026.11.json
    sdlc train status 2026.11                          # each item's stage, release evidence, the train version
    sdlc train notes 2026.11                           # docs/release/trains/2026.11.md from the items' notes
    sdlc train approve 2026.11                         # a person: approves every item's Release gate at once
    sdlc train deployed 2026.11 production --tag       # a person: records the deploy for all, tags once

The train's version is computed like an item's, from the highest bump among its items (breaking → major,
feature or change → minor, bug or task → patch). Each item carries it, so their release notes must say it. An
item in a train has its Release gate approved through the train, not on its own.
"""

import json
import re
from pathlib import Path
from typing import Any

from . import events, release, state, testreport, validate

BUMPS = ("patch", "minor", "major")


class TrainError(Exception):
    pass


def _path(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[\w.-]{1,40}", name):
        raise TrainError("Name the train with letters, digits, dots or dashes, e.g. 2026.11 or november")
    return root / ".sdlc" / "trains" / f"{name}.json"


def load(root: Path, name: str) -> dict[str, Any]:
    p = _path(root, name)
    if not p.exists():
        raise TrainError(f"No train {name}; trains: {', '.join(x.stem for x in p.parent.glob('*.json')) or 'none'}")
    return json.loads(p.read_text())


def _save(root: Path, train: dict[str, Any]) -> None:
    p = _path(root, train["name"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(train, indent=2))


def _set_items(root: Path, name: str, items: list[str], version: str | None) -> None:
    for req in items:
        with state.edit(root, req) as it:
            it["train"] = name
            if version:
                it["version_set"] = version


def create(root: Path, name: str, items: list[str]) -> dict[str, Any]:
    if _path(root, name).exists():
        raise TrainError(f"Train {name} already exists")
    for req in items:
        other = state.load(root, req).get("train")
        if other:
            raise TrainError(f"{req} is already in train {other}")
    train = {"name": name, "items": items, "status": "open", "created": events.now()}
    _save(root, train)
    train["version"] = version(root, train)["version"]
    _save(root, train)
    _set_items(root, name, items, train["version"])
    events.emit(root, "train_created", detail=f"{name}: {', '.join(items)} → {train['version']}")
    return train


def change(root: Path, name: str, add: list[str], remove: list[str]) -> dict[str, Any]:
    train = load(root, name)
    if train["status"] != "open":
        raise TrainError(f"Train {name} is {train['status']}; it can no longer change")
    for req in remove:
        if req in train["items"]:
            train["items"].remove(req)
            with state.edit(root, req) as it:
                it.pop("train", None)
                it.pop("version_set", None)
    for req in add:
        if (other := state.load(root, req).get("train")) and other != name:
            raise TrainError(f"{req} is already in train {other}")
        if req not in train["items"]:
            train["items"].append(req)
    train["version"] = version(root, train)["version"]
    _save(root, train)
    _set_items(root, name, train["items"], train["version"])
    return train


def version(root: Path, train: dict[str, Any]) -> dict[str, Any]:
    """The highest bump among the items, applied once to the latest released version."""
    per_item = {req: validate.next_version(root, req, state.item_type(state.load(root, req))) for req in train["items"]}
    # versions already claimed by this train's own items must not count against it
    base = validate.latest_version_excluding(root, set(train["items"])) or (0, 0, 0)
    bump = max((v["bump"] for v in per_item.values()), key=BUMPS.index, default="patch")
    major, minor, patch = base
    new = ((major + 1, 0, 0) if bump == "major" else (major, minor + 1, 0) if bump == "minor"
           else (major, minor, patch + 1))
    if base == (0, 0, 0):
        new = (0, 1, 0)
    return {"version": "v%d.%d.%d" % new, "bump": bump, "items": {r: v["bump"] for r, v in per_item.items()}}


def status(root: Path, name: str) -> dict[str, Any]:
    train = load(root, name)
    rows = []
    for req in train["items"]:
        it = state.load(root, req)
        stage = it["current_stage"]
        verdicts = {n: testreport.verdict(release.pack(root, req) / f"{n}.md") for n in release.VERDICT_FILES}
        rows.append({"req": req, "title": it["title"], "type": state.item_type(it), "stage": stage or "done",
                     "stage_status": it["stages"][stage]["status"] if stage else "done",
                     "release_evidence": verdicts, "notes_version": release.version(root, req)})
    ready = all(r["stage"] == "release" and r["stage_status"] == state.AWAITING for r in rows) and bool(rows)
    return {**train, "items": rows, "ready_for_approval": ready}


def notes(root: Path, name: str) -> Path:
    train = load(root, name)
    lines = [f"# Release {train['version']} ({name})", "",
             f"Work items: {', '.join(train['items'])}. Generated by `sdlc train notes {name}` from each item's "
             "release notes.", ""]
    sections: dict[str, list[str]] = {}
    for req in train["items"]:
        it = state.load(root, req)
        p = release.pack(root, req) / "release-notes.md"
        text = p.read_text() if p.exists() else ""
        for m in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", re.sub(r"<!--.*?-->", "", text, flags=re.S), re.M | re.S):
            body = [ln for ln in m.group(2).strip().splitlines() if ln.strip() not in ("", "-")]
            if body and m.group(1) != "Approval":
                sections.setdefault(m.group(1), []).append(f"**{req}: {it['title']}**\n" + "\n".join(body))
    for head, parts in sections.items():
        lines += [f"## {head}", "", *("\n\n".join(parts).splitlines()), ""]
    if train.get("approved"):
        lines += ["## Approval", "", train["approved"], ""]
    p = root / "docs" / "release" / "trains" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines))
    return p


def approve(root: Path, cfg: dict[str, Any], name: str, by: str, comment: str, approve_item) -> list[str]:
    """Approve every item's Release gate together (called only for a person, through the human-only path)."""
    st = status(root, name)
    if not st["ready_for_approval"]:
        waiting = [f"{r['req']} ({r['stage']}: {r['stage_status']})" for r in st["items"]
                   if not (r["stage"] == "release" and r["stage_status"] == state.AWAITING)]
        raise TrainError(f"Train {name} is not ready: every item must be waiting at its Release gate; not yet: "
                         + ", ".join(waiting))
    for r in st["items"]:
        if r["notes_version"] != st["version"]:
            raise TrainError(f"{r['req']}'s release notes say {r['notes_version'] or 'no version'}, the train is "
                             f"{st['version']}: `sdlc version {r['req']} --write`")
    done = [approve_item(root, cfg, r["req"], by, f"train {name}: {comment}".strip(": ")) for r in st["items"]]
    train = load(root, name)
    train["status"] = "approved"
    train["approved"] = f"Approved by {by} on {events.now()[:16].replace('T', ' ')}" + (f": {comment}" if comment else "")
    _save(root, train)
    events.emit(root, "train_approved", by=by, detail=f"{name} {train['version']}: {', '.join(train['items'])}")
    return [str(x) for x in done]

