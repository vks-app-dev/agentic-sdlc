"""Work items (REQ-xxx) moving through the stage state machine.

Each work item is one JSON file in .sdlc/state/. Every change goes through
`edit()`, which holds an exclusive file lock so parallel agents calling the
CLI at the same time cannot overwrite each other.
"""

import fcntl
import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config, events
from .design import files as design_files
from .stages import BY_NAME, TOGGLEABLE, SIZES, STAGE_NAMES, WORK_TYPES

STATE_DIR = Path(".sdlc") / "state"

# Stage statuses
PENDING, IN_PROGRESS, AWAITING, APPROVED, SKIPPED, BLOCKED = (
    "pending", "in_progress", "awaiting_approval", "approved", "skipped", "blocked")
# Task statuses
T_PENDING, T_RUNNING, T_REVIEW, T_DONE, T_FAILED, T_BLOCKED = (
    "pending", "running", "in_review", "done", "failed", "blocked")
CODE_STAGES = ("development", "qa")  # stages where tasks are built (QA adds defect fixes)
# Defects
SEVERITIES = ("critical", "high", "medium", "low")
D_OPEN = ("open", "fixing", "fixed", "reopened")  # not yet resolved
D_ACTIONS = {"fix": "fixing", "fixed": "fixed", "verified": "verified", "reopen": "reopened",
             "duplicate": "duplicate", "defer": "deferred", "accept": "accepted"}
HUMAN_ONLY = ("defer", "accept")  # deciding to ship with a known defect is a human decision


class StateError(Exception):
    pass


def _path(root: Path, req: str) -> Path:
    return root / STATE_DIR / f"{req}.json"


def list_ids(root: Path) -> list[str]:
    d = root / STATE_DIR
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("REQ-*.json"))


def _upgrade(item: dict[str, Any]) -> dict[str, Any]:
    """Items made before a stage existed (e.g. Planning) get it: skipped if already past it, else pending."""
    if all(s in item["stages"] for s in STAGE_NAMES):
        return item
    cur = item.get("current_stage")
    pos = STAGE_NAMES.index(cur) if cur in STAGE_NAMES else len(STAGE_NAMES)
    stages = {}
    for i, s in enumerate(STAGE_NAMES):
        stages[s] = item["stages"].get(s) or {
            "status": PENDING if i > pos else SKIPPED, "gate": list(BY_NAME[s].default_gate), "artifacts": [],
            "feedback": [], **({} if i > pos else {"summary": "Stage added after this item passed it"})}
    item["stages"] = stages
    return item


def load(root: Path, req: str) -> dict[str, Any]:
    p = _path(root, req)
    if not p.exists():
        raise StateError(f"{req} not found. Known: {', '.join(list_ids(root)) or 'none'}")
    return _upgrade(json.loads(p.read_text()))


def load_all(root: Path) -> list[dict[str, Any]]:
    return [load(root, r) for r in list_ids(root)]


@contextmanager
def edit(root: Path, req: str) -> Iterator[dict[str, Any]]:
    p = _path(root, req)
    if not p.exists():
        raise StateError(f"{req} not found")
    with open(p, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        item = _upgrade(json.load(f))
        yield item
        item["updated"] = events.now()
        f.seek(0)
        f.truncate()
        json.dump(item, f, indent=2)


def _history(item: dict[str, Any], what: str, **kw: Any) -> None:
    item["history"].append({"ts": events.now(), "what": what, **kw})


def _enabled(cfg: dict[str, Any], stage: str) -> bool:
    return cfg["stages"].get(stage, {}).get("enabled", True)


def item_type(item: dict[str, Any]) -> str:
    return item.get("type", "feature")  # items made before work types existed are features


def runs(item: dict[str, Any], cfg: dict[str, Any], stage: str) -> bool:
    """Does this stage run for this item? Config switch, then the work type, then (Design) the size."""
    return _skip_reason(item, cfg, stage) is None


def _skip_reason(item: dict[str, Any], cfg: dict[str, Any], stage: str) -> str | None:
    if not _enabled(cfg, stage):
        return "turned off in sdlc.config.yaml"
    wt = config.work_type(cfg, item_type(item))
    if stage in TOGGLEABLE and stage not in wt["stages"]:
        return f"not part of a {wt['title'].lower()}"
    sizes = wt["design_if_size"]
    if stage in ("design", "planning") and sizes and item.get("size") and item["size"] not in sizes:
        return (f"size {item['size']}: a {wt['title'].lower()} needs {BY_NAME[stage].title} only when size is "
                f"{'/'.join(sizes)}")
    return None


UI_FRAMEWORKS = {"react", "next", "vue", "nuxt", "svelte", "sveltekit", "angular", "solid", "remix", "astro",
                 "react-native", "flutter", "django", "rails"}


def has_ui(root: Path, item: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """Does this work item have a user interface to test? The design's ux.md decides; else the stack."""
    if (root / "docs" / "design" / item["id"] / "ux.md").exists():
        return True
    if any(c["kind"] in ("frontend", "fullstack") for c in config.components(cfg)):
        return True
    stack = cfg.get("stack", {})
    if "ui" in stack:  # explicit override in sdlc.config.yaml
        return bool(stack["ui"])
    return str(stack.get("framework") or "").lower() in UI_FRAMEWORKS


# ---- Dependencies between work items ----

def blockers(item: dict[str, Any]) -> list[str]:
    return [link["req"] for link in item.get("links", []) if link["rel"] == "blocked_by"]


def _finished(item: dict[str, Any]) -> bool:
    """A blocker is finished once its code is merged: Development approved (or the whole item done)."""
    return item["status"] == "done" or item["stages"]["development"]["status"] in (APPROVED, SKIPPED)


def open_blockers(root: Path, item: dict[str, Any]) -> list[str]:
    return [b for b in blockers(item) if not _finished(load(root, b))]


def depend(root: Path, req: str, on: str) -> list[str]:
    """Record that `req` cannot start Development until `on` is merged. Refuses cycles."""
    if req == on:
        raise StateError(f"{req} cannot depend on itself")
    load(root, on)
    seen, todo = set(), [on]
    while todo:  # walk on's blockers: reaching req means a cycle
        cur = todo.pop()
        if cur == req:
            raise StateError(f"{req} → {on} would create a dependency cycle")
        if cur not in seen:
            seen.add(cur)
            todo += blockers(load(root, cur))
    with edit(root, req) as item:
        if on not in blockers(item):
            item.setdefault("links", []).append({"rel": "blocked_by", "req": on})
            _history(item, "dependency_added", on=on)
        result = blockers(item)
    events.emit(root, "dependency_added", req=req, detail=f"waits for {on}")
    return result


def _gate(cfg: dict[str, Any], wtype: str, stage: str) -> list[str]:
    gates = config.work_type(cfg, wtype)["gates"]
    return list(gates[stage]) if stage in gates else list(cfg["stages"].get(stage, {}).get("gate", []))


def _fresh_stages(item: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    return {s: {"status": PENDING if runs(item, cfg, s) else SKIPPED, "gate": _gate(cfg, item_type(item), s),
                "artifacts": [], "feedback": []} for s in STAGE_NAMES}


def _check_type(wtype: str) -> None:
    if wtype not in WORK_TYPES:
        raise StateError(f"Unknown work type '{wtype}'. Valid: {', '.join(WORK_TYPES)}")


def new_item(root: Path, cfg: dict[str, Any], title: str, wtype: str = "feature",
             links: list[dict[str, str]] | None = None) -> dict[str, Any]:
    _check_type(wtype)
    links = list({(x["rel"], x["req"]): x for x in links or []}.values())
    for link in links or []:
        load(root, link["req"])  # a link must point at a known work item
    d = root / STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    lock = open(d / ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        nums = [int(m.group(1)) for r in list_ids(root) if (m := re.match(r"REQ-(\d+)", r))]
        req = f"REQ-{(max(nums) + 1) if nums else 1:03d}"
        ts = events.now()
        item = {"id": req, "title": title, "type": wtype, "links": links or [], "size": None,
                "created": ts, "updated": ts, "status": "active"}
        first = next((s for s in STAGE_NAMES if runs(item, cfg, s)), None)
        if first is None:
            raise StateError(f"No stage runs for a {wtype}: check work_types and stages in sdlc.config.yaml")
        item.update(current_stage=first, stages=_fresh_stages(item, cfg), tasks=[], history=[])
        _history(item, "created", title=title, type=wtype)
        _path(root, req).write_text(json.dumps(item, indent=2))
    finally:
        lock.close()
    events.emit(root, "item_created", req=req, stage=first, detail=f"{wtype}: {title}", status=PENDING)
    return item


def _require_current(item: dict[str, Any], stage: str | None) -> str:
    cur = item["current_stage"]
    if stage and stage != cur:
        raise StateError(f"{item['id']} is at '{cur}', not '{stage}'")
    if cur is None:
        raise StateError(f"{item['id']} has finished all stages")
    return cur


def start_stage(root: Path, req: str, stage: str | None = None) -> str:
    with edit(root, req) as item:
        cur = _require_current(item, stage)
        st = item["stages"][cur]
        if st["status"] == AWAITING:
            raise StateError(f"{req}/{cur} is waiting for approval; approve or reject it first")
        if cur == "development" and (waiting := open_blockers(root, item)):
            raise StateError(f"{req} waits for {', '.join(waiting)}: their Development must be approved first")
        st["status"] = IN_PROGRESS
        st.setdefault("started", events.now())
        item["status"] = "active"
        _history(item, "stage_started", stage=cur)
        single = cur == "development" and not item["tasks"]
        if single:  # no Design (bug, task, small change): the whole item is one task
            item["tasks"] = [_new_task(req, {"id": "T1", "title": item["title"]})]
            _history(item, "plan_set", tasks=["T1"], auto=True)
    events.emit(root, "stage_started", req=req, stage=cur, status=IN_PROGRESS)
    if single:
        events.emit(root, "plan_set", req=req, stage=cur, detail="1 task: T1 (no Design, whole item)")
    return cur


def add_artifact(root: Path, req: str, path: str, stage: str | None = None) -> None:
    with edit(root, req) as item:
        cur = stage or item["current_stage"]
        arts = item["stages"][cur]["artifacts"]
        if path not in arts:
            arts.append(path)
    events.emit(root, "artifact", req=req, stage=cur, detail=path)


def complete_stage(root: Path, req: str, cfg: dict[str, Any], summary: str = "") -> str:
    """Agents finished the stage: wait at the gate, or advance if it has no gate."""
    with edit(root, req) as item:
        cur = _require_current(item, None)
        if cur in CODE_STAGES:
            open_tasks = [t["id"] for t in item["tasks"] if t["status"] != T_DONE]
            if open_tasks:
                raise StateError(f"{req}: tasks not done yet: {', '.join(open_tasks)}")
            if cfg["stages"]["development"].get("require_checks", True):
                _require_checks(item)
        if cur in ("release", "production"):
            _require_release(root, item, cfg, cur)
        if cur == "qa" and (blocking := open_blocker_defects(item)):
            raise StateError(f"{req}: release-blocking defects still open: {', '.join(blocking)}. Fix and verify "
                             f"them, or a human defers or accepts them")
        st = item["stages"][cur]
        st["summary"] = summary
        if st["gate"]:
            st["status"] = AWAITING
            st["awaiting_since"] = events.now()
            _history(item, "gate_waiting", stage=cur, approvers=st["gate"])
            new_status = AWAITING
        else:
            st["status"] = APPROVED
            st["ended"] = events.now()
            _history(item, "stage_done", stage=cur)
            _advance(item, cfg)
            new_status = APPROVED
    events.emit(root, "gate_waiting" if new_status == AWAITING else "stage_done",
                req=req, stage=cur, status=new_status, detail=summary or None)
    return new_status


def _require_release(root: Path, item: dict[str, Any], cfg: dict[str, Any], stage: str) -> None:
    from . import release, testreport  # late: both import state

    req = item["id"]
    if stage == "release":
        bad = [f"{n}.md: {v}" for n in release.VERDICT_FILES
               if (v := testreport.verdict(release.pack(root, req) / f"{n}.md")) not in ("PASS", "N/A")]
        if bad:
            raise StateError(f"{req}: release evidence not ready: {'; '.join(bad)}")
        if "staging" in release.environments(cfg):
            s = release.last(item, "smoke", "staging")
            if not (release.last(item, "deployments", "staging") and s and s["ok"]):
                raise StateError(f"{req}: deploy to staging and pass its smoke check first "
                                 f"(`sdlc deploy {req} staging`, `sdlc smoke {req} staging`)")
        if not release.version(root, req):
            raise StateError(f"{req}: set the version on the first line of docs/release/{req}/release-notes.md")
    else:
        d = release.last(item, "deployments", "production")
        if not d:
            raise StateError(f"{req}: a person deploys to production and records it: `sdlc deployed {req} production`")
        s = release.last(item, "smoke", "production")
        if not (s and s["ok"] and s["ts"] >= d["ts"]):
            raise StateError(f"{req}: run the post-deployment check after deploying: `sdlc smoke {req} production`")


def _require_checks(item: dict[str, Any]) -> None:
    """Development is complete only when `sdlc check` passed on the code as it is after the last merge."""
    checks = item.get("checks")
    if not checks:
        raise StateError(f"{item['id']}: run `sdlc check {item['id']}` first (lint, tests, security checks)")
    if not checks["passed"]:
        failed = ", ".join(r["name"] for r in checks["results"] if r["status"] != "passed")
        raise StateError(f"{item['id']}: the last `sdlc check` failed ({failed}); fix it and run it again")
    if checks.get("merges", 0) < item.get("merges", 0):  # a counter, not a clock: merges can share a second
        raise StateError(f"{item['id']}: code was merged after the last `sdlc check`; run it again")


def set_pr(root: Path, req: str, pr: dict[str, Any]) -> None:
    with edit(root, req) as item:
        new = not item.get("pr")
        item["pr"] = {**(item.get("pr") or {}), **pr, "updated": events.now()}
        _history(item, "pr_opened" if new else "pr_updated", url=pr["url"], head=pr["head"])
    events.emit(root, "pr_opened" if new else "pr_updated", req=req, detail=f"{pr['url']} @ {pr['head']}")


def record_checks(root: Path, req: str, report: dict[str, Any]) -> None:
    with edit(root, req) as item:
        item["checks"] = {"ts": report["ts"], "head": report["head"], "passed": report["passed"],
                          "merges": item.get("merges", 0),
                          "results": [{"name": r["name"], "status": r["status"]} for r in report["results"]]}
        _history(item, "checks_run", passed=report["passed"])
    failed = [r["name"] for r in report["results"] if r["status"] != "passed"]
    events.emit(root, "checks_run", req=req, stage="development", status="passed" if report["passed"] else "failed",
                detail="all passed" if report["passed"] else f"failed: {', '.join(failed)}")


def _append_task(item: dict[str, Any], title: str, covers: list[str], deps: list[str] | None = None) -> str:
    if item["current_stage"] not in CODE_STAGES:
        raise StateError(f"{item['id']} is at '{item['current_stage']}'; fix tasks are added during Development or QA")
    ids = {t["id"] for t in item["tasks"]}
    unknown = set(deps or []) - ids
    if unknown:
        raise StateError(f"Unknown task(s): {', '.join(sorted(unknown))}")
    n = max((int(m.group(1)) for i in ids if (m := re.match(r"T(\d+)$", i))), default=0) + 1
    task = _new_task(item["id"], {"id": f"T{n}", "title": title, "deps": deps or [], "acceptance": covers, "area": "fix"})
    item["tasks"].append(task)
    _history(item, "task_added", task=task["id"], title=title)
    return task["id"]


def add_task(root: Path, req: str, title: str, covers: list[str], deps: list[str] | None = None) -> str:
    """A fix task found after the plan ran (integration, verification or QA failure). Goes through coder + review."""
    with edit(root, req) as item:
        tid = _append_task(item, title, covers, deps)
        stage = item["current_stage"]
    events.emit(root, "task_added", req=req, stage=stage, task=tid, detail=title)
    return tid


# ---- Defects (QA) ----

def open_blocker_defects(item: dict[str, Any]) -> list[str]:
    return [d["id"] for d in item.get("defects", []) if d["blocker"] and d["status"] in D_OPEN]


def add_defect(root: Path, req: str, title: str, severity: str, blocker: bool, covers: list[str],
               found_by: str, note: str = "") -> str:
    if severity not in SEVERITIES:
        raise StateError(f"Severity must be one of {', '.join(SEVERITIES)}")
    with edit(root, req) as item:
        defects = item.setdefault("defects", [])
        did = f"D{len(defects) + 1}"
        defects.append({"id": did, "title": title, "severity": severity, "blocker": blocker, "covers": covers,
                        "status": "open", "found_by": found_by, "task": None, "stage": item["current_stage"],
                        "log": [{"ts": events.now(), "action": "found", "note": note, "by": found_by}]})
        _history(item, "defect_found", defect=did, title=title, severity=severity, blocker=blocker)
    events.emit(root, "defect_found", req=req, stage="qa", agent=found_by or None, status=severity,
                detail=f"{did}{' (blocker)' if blocker else ''}: {title}")
    return did


def update_defect(root: Path, req: str, did: str, action: str = "update", *, severity: str | None = None,
                  blocker: bool | None = None, note: str = "", by: str = "", of: str | None = None) -> dict[str, Any]:
    """Triage (severity, blocker), start a fix (creates a task), verify, reopen, mark duplicate, defer or accept."""
    if action != "update" and action not in D_ACTIONS:
        raise StateError(f"Unknown defect action '{action}'. Valid: update, {', '.join(D_ACTIONS)}")
    if severity and severity not in SEVERITIES:
        raise StateError(f"Severity must be one of {', '.join(SEVERITIES)}")
    if action in HUMAN_ONLY and not note.strip():
        raise StateError(f"Say why the defect is {D_ACTIONS[action]} (-m)")
    with edit(root, req) as item:
        d = next((x for x in item.get("defects", []) if x["id"] == did), None)
        if d is None:
            raise StateError(f"{req} has no defect {did}")
        if severity:
            d["severity"] = severity
        if blocker is not None:
            d["blocker"] = blocker
        if action == "fix":
            if d["status"] not in ("open", "reopened"):
                raise StateError(f"{did} is '{d['status']}'; only open or reopened defects get a fix task")
            d["task"] = _append_task(item, f"{did}: {d['title']}", d["covers"] or [did])
        if action == "duplicate":
            if not of or not any(x["id"] == of for x in item["defects"]):
                raise StateError(f"Name the defect {did} duplicates: --of D<n>")
            d["duplicate_of"] = of
        if action != "update":
            d["status"] = D_ACTIONS[action]
        d["log"].append({"ts": events.now(), "action": action, "note": note, "by": by})
        _history(item, f"defect_{action}", defect=did, by=by, comment=note)
        out = dict(d)
    events.emit(root, f"defect_{action}", req=req, stage="qa", by=by or None, status=out["status"],
                detail=f"{did}: {note or out['title']}")
    return out


def _advance(item: dict[str, Any], cfg: dict[str, Any]) -> None:
    idx = STAGE_NAMES.index(item["current_stage"])
    for nxt in STAGE_NAMES[idx + 1:]:
        if runs(item, cfg, nxt):
            item["current_stage"] = nxt
            return
        st = item["stages"][nxt]
        if st["status"] == PENDING:  # e.g. Design for a small change, decided by the size set in Requirements
            st["status"], st["summary"] = SKIPPED, f"Skipped: {_skip_reason(item, cfg, nxt)}"
            _history(item, "stage_skipped", stage=nxt, reason=st["summary"])
    item["current_stage"] = None
    item["status"] = "done"


def approve(root: Path, req: str, cfg: dict[str, Any], by: str, comment: str = "") -> str | None:
    with edit(root, req) as item:
        cur = _require_current(item, None)
        st = item["stages"][cur]
        if st["status"] != AWAITING:
            raise StateError(f"{req}/{cur} is '{st['status']}', not waiting for approval")
        st["status"] = APPROVED
        st["ended"] = events.now()
        st["decision"] = {"result": "approved", "by": by, "comment": comment, "at": events.now()}
        _history(item, "gate_approved", stage=cur, by=by, comment=comment)
        _advance(item, cfg)
        nxt = item["current_stage"]
    events.emit(root, "gate_approved", req=req, stage=cur, status=APPROVED, detail=comment or None, by=by)
    if nxt is None:
        events.emit(root, "item_done", req=req, status="done")
    return nxt


def reject(root: Path, req: str, by: str, comment: str) -> None:
    """Send the stage back to the agents with feedback."""
    with edit(root, req) as item:
        cur = _require_current(item, None)
        st = item["stages"][cur]
        if st["status"] != AWAITING:
            raise StateError(f"{req}/{cur} is '{st['status']}', not waiting for approval")
        st["status"] = IN_PROGRESS
        st["feedback"].append({"by": by, "comment": comment, "at": events.now()})
        _history(item, "gate_rejected", stage=cur, by=by, comment=comment)
    events.emit(root, "gate_rejected", req=req, stage=cur, status=IN_PROGRESS, detail=comment, by=by)


def skip_stage(root: Path, req: str, cfg: dict[str, Any], reason: str) -> str | None:
    with edit(root, req) as item:
        cur = _require_current(item, None)
        item["stages"][cur]["status"] = SKIPPED
        item["stages"][cur]["summary"] = reason
        _history(item, "stage_skipped", stage=cur, reason=reason)
        _advance(item, cfg)
        nxt = item["current_stage"]
    events.emit(root, "stage_skipped", req=req, stage=cur, status=SKIPPED, detail=reason)
    return nxt


def set_size(root: Path, req: str, size: str) -> None:
    """Record the S/M/L size from Requirements. For a change it decides whether Design runs."""
    size = size.upper()
    if size not in SIZES:
        raise StateError(f"Size must be one of {', '.join(SIZES)}")
    with edit(root, req) as item:
        if item["stages"]["design"]["status"] not in (PENDING, SKIPPED):
            raise StateError(f"{req}: Design has already started; the size can no longer change the plan")
        item["size"] = size
        _history(item, "size_set", size=size)
    events.emit(root, "size_set", req=req, detail=size)


def retype(root: Path, req: str, cfg: dict[str, Any], wtype: str, by: str, reason: str) -> str:
    """A human changes the work type (e.g. triage found the bug is really a change).

    Allowed until Development starts. The item restarts at the new type's first stage; earlier
    decisions stay in the history, and the spec keeps its text (the CLI re-templates it).
    """
    _check_type(wtype)
    with edit(root, req) as item:
        old = item_type(item)
        if old == wtype:
            raise StateError(f"{req} is already a {wtype}")
        if item["stages"]["development"]["status"] not in (PENDING, SKIPPED):
            raise StateError(f"{req}: Development has started, so the type is fixed. Create a new work item "
                             f"linked to this one instead.")
        artifacts = {s: st["artifacts"] for s, st in item["stages"].items()}
        item["type"] = wtype
        item["stages"] = _fresh_stages(item, cfg)
        for s, arts in artifacts.items():
            item["stages"][s]["artifacts"] = arts
        item["tasks"] = []
        item["status"] = "active"
        item["current_stage"] = next((s for s in STAGE_NAMES if item["stages"][s]["status"] == PENDING), None)
        _history(item, "retyped", **{"from": old, "to": wtype}, by=by, comment=reason)
        first = item["current_stage"]
    events.emit(root, "retyped", req=req, stage=first, by=by, detail=f"{old} → {wtype}: {reason}")
    return first or "none"


# ---- Task DAG (development stage) ----

def set_plan(root: Path, req: str, tasks: list[dict[str, Any]]) -> None:
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise StateError("Task ids must be unique")
    for t in tasks:
        for d in t.get("deps", []):
            if d not in ids:
                raise StateError(f"Task {t['id']} depends on unknown task {d}")
    _check_acyclic(tasks)
    with edit(root, req) as item:
        item["tasks"] = [_new_task(req, t) for t in tasks]
        _history(item, "plan_set", tasks=ids)
        stage = item["current_stage"]
    events.emit(root, "plan_set", req=req, stage=stage, detail=f"{len(tasks)} tasks: {', '.join(ids)}")


def _new_task(req: str, t: dict[str, Any]) -> dict[str, Any]:
    return {"id": t["id"], "title": t["title"], "deps": t.get("deps", []), "files": t.get("files", []),
            "acceptance": t.get("acceptance", []), "area": t.get("area", ""), "size": t.get("size", ""),
            "component": t.get("component", ""),
            "status": T_PENDING, "attempts": 0, "branch": f"sdlc/{req}-{t['id']}", "log": []}


def waves(tasks: list[dict[str, Any]]) -> list[list[str]]:
    """Tasks grouped by the earliest round they can run in (all their deps in earlier rounds)."""
    level: dict[str, int] = {}
    deps = {t["id"]: set(t.get("deps", [])) for t in tasks}
    while len(level) < len(deps):
        for tid, ds in deps.items():
            if tid not in level and ds <= level.keys():
                level[tid] = 1 + max((level[d] for d in ds), default=-1)
    out: list[list[str]] = [[] for _ in range(max(level.values(), default=-1) + 1)]
    for t in tasks:
        out[level[t["id"]]].append(t["id"])
    return out


def _check_acyclic(tasks: list[dict[str, Any]]) -> None:
    deps = {t["id"]: set(t.get("deps", [])) for t in tasks}
    done: set[str] = set()
    while deps:
        ready = [k for k, v in deps.items() if v <= done]
        if not ready:
            raise StateError(f"Task plan has a dependency cycle among: {', '.join(sorted(deps))}")
        for k in ready:
            done.add(k)
            del deps[k]


def ready_tasks(item: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Tasks whose dependencies are done, capped so at most parallel_coders run at once."""
    cap = cfg["stages"]["development"].get("parallel_coders", 1)
    running = sum(1 for t in item["tasks"] if t["status"] in (T_RUNNING, T_REVIEW))
    done = {t["id"] for t in item["tasks"] if t["status"] == T_DONE}
    ready = [t for t in item["tasks"]
             if t["status"] in (T_PENDING, T_FAILED) and set(t["deps"]) <= done]
    return ready[:max(cap - running, 0)]


def _task(item: dict[str, Any], task_id: str) -> dict[str, Any]:
    for t in item["tasks"]:
        if t["id"] == task_id:
            return t
    raise StateError(f"{item['id']} has no task {task_id}")


def update_task(root: Path, req: str, cfg: dict[str, Any], task_id: str, action: str, note: str = "") -> str:
    max_attempts = cfg["stages"]["development"].get("max_attempts", 3)
    with edit(root, req) as item:
        t = _task(item, task_id)
        where = item["current_stage"] if item["current_stage"] in CODE_STAGES else "development"
        if action == "start":
            if t["status"] not in (T_PENDING, T_FAILED):
                raise StateError(f"{task_id} is '{t['status']}', cannot start")
            done = {x["id"] for x in item["tasks"] if x["status"] == T_DONE}
            missing = set(t["deps"]) - done
            if missing:
                raise StateError(f"{task_id} waits on {', '.join(sorted(missing))}")
            cap = cfg["stages"]["development"].get("parallel_coders", 1)
            running = [x["id"] for x in item["tasks"] if x["status"] in (T_RUNNING, T_REVIEW)]
            if len(running) >= cap:
                raise StateError(f"parallel_coders={cap} reached ({', '.join(running)} running); "
                                 f"finish one first")
            t["status"] = T_RUNNING
            t["attempts"] += 1
        elif action == "review":
            t["status"] = T_REVIEW
        elif action == "done":
            t["status"] = T_DONE
            item["merges"] = item.get("merges", 0) + 1
            for d in item.get("defects", []):  # its defect now waits for a QA retest
                if d["task"] == task_id and d["status"] == "fixing":
                    d["status"] = "fixed"
                    d["log"].append({"ts": events.now(), "action": "fixed", "note": f"{task_id} merged", "by": ""})
        elif action == "fail":
            t["status"] = T_BLOCKED if t["attempts"] >= max_attempts else T_FAILED
            if t["status"] == T_BLOCKED:
                item["stages"][where]["status"] = BLOCKED
                item["status"] = "blocked"
        elif action == "unblock":
            t["status"] = T_PENDING
            t["attempts"] = 0
            if item["stages"][where]["status"] == BLOCKED:
                item["stages"][where]["status"] = IN_PROGRESS
                item["status"] = "active"
        else:
            raise StateError(f"Unknown task action '{action}'")
        t["log"].append({"ts": events.now(), "action": action, "note": note})
        status = t["status"]
    events.emit(root, f"task_{action}", req=req, stage=where, task=task_id,
                status=status, detail=note or None)
    return status


# ---- What should the orchestrator do next? ----

def next_action(root: Path, cfg: dict[str, Any], req: str) -> dict[str, Any]:
    item = load(root, req)
    cur = item["current_stage"]
    if cur is None:
        return {"req": req, "action": "done", "message": f"{req} has completed every stage."}
    st = item["stages"][cur]
    stage = BY_NAME[cur]
    wt = config.work_type(cfg, item_type(item))
    scfg = cfg["stages"].get(cur, {})
    base = {"req": req, "title": item["title"], "type": wt["name"], "links": item.get("links", []),
            "compliance": cfg.get("project", {}).get("compliance") or [],
            "size": item.get("size"), "stage": cur, "stage_title": wt["stage_titles"].get(cur, stage.title),
            "autonomy": scfg.get("autonomy", "L2"), "gate": st["gate"]}

    if item["status"] == "blocked" or st["status"] == BLOCKED:
        blocked = [t["id"] for t in item["tasks"] if t["status"] == T_BLOCKED]
        return {**base, "action": "human_needed",
                "message": f"Blocked after repeated failures: {', '.join(blocked) or cur}. "
                           f"Fix or unblock with `sdlc task {req} <task> unblock`."}
    if st["status"] == AWAITING:
        return {**base, "action": "await_gate",
                "message": f"Waiting for approval from {', '.join(st['gate'])}. "
                           f"Run `sdlc approve {req}` or `sdlc reject {req} -m \"why\"`."}
    if cur == "development" and st["status"] == PENDING and (waiting := open_blockers(root, item)):
        return {**base, "action": "waiting", "blocked_by": waiting,
                "message": f"Waits for {', '.join(waiting)}: Development starts when their Development is approved."}
    if cur == "production":
        from . import release
        e = release.environments(cfg).get("production") or {}
        deployed = release.last(item, "deployments", "production")
        steps = (f"1) merge the pull request{' #' + str(item['pr']['number']) if item.get('pr') else ''}; "
                 f"2) deploy: `{e.get('deploy') or 'your production deploy'}`; "
                 f"3) `sdlc deployed {req} production --version {release.version(root, req) or 'vX.Y.Z'}`; "
                 f"4) `sdlc smoke {req} production`; 5) `sdlc complete {req}`")
        return {**base, "action": "human_deploy", "deployed": deployed,
                "message": "Production is deployed by a person, never by an agent. " + steps}
    if not stage.automated:
        return {**base, "action": "manual",
                "message": f"{stage.title} has no agents in the MVP. Do it by hand, then "
                           f"`sdlc complete {req}` (or `sdlc skip {req} -m \"why\"`)."}

    ui = has_ui(root, item, cfg)
    from . import standards

    steps = [{"agent": s.agent, "model": config.agent_model(cfg, s.agent), "does": s.does,
              "output": s.output.format(req=req), "parallel_group": s.parallel_group,
              "standards": standards.for_agent(root, cfg, s.agent)}
             for s in wt["steps"].get(cur, stage.steps) if s.when != "ui" or ui]
    out = {**base, "action": "run_stage", "stage_status": st["status"], "steps": steps,
           "feedback": st["feedback"][-1]["comment"] if st["feedback"] else None,
           "inputs": [p for p in _inputs_for(cur, item) if (root / p).exists()]}
    if cur == "qa":
        defects = item.get("defects", [])
        out["defects"] = [{k: d[k] for k in ("id", "title", "severity", "blocker", "status", "task")} for d in defects]
        out["open_blockers"] = open_blocker_defects(item)
        out["ready_tasks"] = [{"id": t["id"], "title": t["title"], "branch": t["branch"], "files": t["files"],
                               "attempt": t["attempts"] + 1} for t in ready_tasks(item, cfg)]
        out["to_retest"] = [d["id"] for d in defects if d["status"] == "fixed"]
    if cur == "development":
        if not item["tasks"]:
            out["note"] = "No task plan (no Design): `sdlc start` makes the whole item one task, T1."
        comps = {c["name"]: c for c in config.components(cfg)}
        out["ready_tasks"] = [{"id": t["id"], "title": t["title"], "branch": t["branch"], "files": t["files"],
                               "attempt": t["attempts"] + 1, "component": t.get("component", ""),
                               **({"path": comps[t["component"]]["path"], "commands": comps[t["component"]]["commands"]}
                                  if t.get("component") in comps else {})}
                              for t in ready_tasks(item, cfg)]
        if len(comps) > 1:
            out["components"] = [{k: c[k] for k in ("name", "path", "kind")} for c in comps.values()]
        out["tasks"] = [{"id": t["id"], "status": t["status"], "deps": t["deps"]} for t in item["tasks"]]
        out["parallel_coders"] = cfg["stages"]["development"].get("parallel_coders", 1)
        if item["tasks"] and all(t["status"] == T_DONE for t in item["tasks"]):
            out["action"] = "complete_stage"
            out["message"] = f"All tasks done. Run `sdlc complete {req} -m \"summary\"`."
    return out


def _inputs_for(stage: str, item: dict[str, Any]) -> list[str]:
    """Files the stage's agents read. Linked items' specs come along so agents see what already exists."""
    req = item["id"]
    spec = f"specs/{req}.md"
    linked = [p for link in item.get("links", []) for p in (f"specs/{link['req']}.md", *design_files(link["req"]))]
    product = [f"docs/product/{n}.md" for n in ("overview", "data", "integrations", "compliance")]
    architecture = ["docs/architecture/overview.md", "docs/architecture/data-model.md"]
    plan = [f"docs/plan/{req}/{n}.md" for n in ("README", "test-plan", "release-plan")]
    own = {"requirements": [spec, *product],
           "design": [spec, *product, *architecture, *design_files(req)],
           "planning": [spec, *design_files(req), *plan, *product, *architecture],
           "development": [spec, *design_files(req), *plan, f".sdlc/plans/{req}.json", *architecture],
           "release": [spec, *plan, *(f"docs/qa/{req}/{n}.md" for n in ("summary", "defects")),
                       f"docs/dev/{req}/handover.md", f"docs/dev/{req}/checks.md", *design_files(req)],
           "qa": [spec, *design_files(req), *plan, *(f"docs/dev/{req}/{n}.md" for n in ("handover", "verification", "checks")),
                  f"docs/rtm/{req}.md"]}.get(stage, [])
    return ["sdlc.config.yaml", "CLAUDE.md", *own, *linked] if stage in (*TOGGLEABLE, "qa", "release") else []
