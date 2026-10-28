"""Dashboard API: reads .sdlc/ in one repo, streams events live, and records human gate decisions.

Run with `sdlc dashboard`. Binds to localhost only: approving a gate from the UI is a human action.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import assistant, blockers, config, events, generate, gitops, human, release, standards, state, usage
from ..cli import _who, new_work, run_human
from ..cli import defect as defect_item
from ..stages import STAGES, WORK_TYPES
from ..paths import plugin_dir
from .runner import Runner, auth_status, start_login

STATIC = Path(__file__).parent / "static"
READABLE = {".md", ".json", ".yaml", ".yml", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
            ".java", ".kt", ".rb", ".sql", ".toml", ".css", ".html", ".sh"}
MAX_FILE = 512 * 1024


class Decision(BaseModel):
    by: str | None = None
    comment: str = ""


class DefectDecision(BaseModel):
    action: str  # defer | accept | reopen
    by: str | None = None
    comment: str = ""


class Ask(BaseModel):
    text: str


class NewWork(BaseModel):
    text: str
    title: str
    type: str
    links: list[dict[str, str]] = []
    run: bool = True
    by: str | None = None


class LibUpload(BaseModel):
    content: str
    filename: str = ""


class LibNew(BaseModel):
    name: str
    title: str = ""
    agents: list[str] | Literal["all"] = []
    skill: bool = False
    content: str
    filename: str = ""


class SpecValues(BaseModel):
    values: dict[str, str]


class CommitSetup(BaseModel):
    files: list[str]
    message: str
    target: str


class Answer(BaseModel):
    accepted: bool
    note: str = ""


class Retype(BaseModel):
    type: str
    by: str | None = None
    comment: str = ""


def _ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def live_agents(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Subagents that started and have not stopped, with their latest action."""
    agents: dict[str, dict[str, Any]] = {}
    for e in evs:
        aid = e.get("agent_id")
        if not aid:
            continue
        if e["event"] == "agent_start":
            agents[aid] = {"agent_id": aid, "agent": e.get("agent"), "req": e.get("req"), "task": e.get("task"),
                           "started": e["ts"], "last": None, "last_ts": e["ts"], "actions": 0}
        elif aid in agents:
            if e["event"] == "agent_stop":
                del agents[aid]
            elif e["event"] == "tool_use":
                a = agents[aid]
                a.update(last=f"{e.get('tool', '')}: {e.get('detail', '')}", last_ts=e["ts"], actions=a["actions"] + 1)
    return sorted(agents.values(), key=lambda a: a["started"])


def metrics(items: list[dict[str, Any]], evs: list[dict[str, Any]]) -> dict[str, Any]:
    per_stage: dict[str, list[float]] = {s.name: [] for s in STAGES}
    waits: list[float] = []
    for it in items:
        for name, st in it["stages"].items():
            start, end = _ts(st.get("started")), _ts(st.get("ended"))
            if start and end:
                per_stage[name].append((end - start).total_seconds() / 60)
            if st.get("awaiting_since") and st.get("decision"):
                waits.append((_ts(st["decision"]["at"]) - _ts(st["awaiting_since"])).total_seconds() / 60)
    approvals = sum(e["event"] == "gate_approved" for e in evs)
    rejections = sum(e["event"] == "gate_rejected" for e in evs)
    tasks = [t for it in items for t in it["tasks"]]
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    by_type = []
    for name, w in WORK_TYPES.items():
        mine = [i for i in items if state.item_type(i) == name]
        cycle = [(_ts(i["updated"]) - _ts(i["created"])).total_seconds() / 3600 for i in mine if i["status"] == "done"]
        by_type.append({"type": name, "title": w.title, "total": len(mine),
                        "open": sum(i["status"] != "done" for i in mine),
                        "done": len(cycle), "avg_cycle_h": avg(cycle)})
    return {
        "items": {"total": len(items), "active": sum(i["status"] == "active" for i in items),
                  "blocked": sum(i["status"] == "blocked" for i in items),
                  "done": sum(i["status"] == "done" for i in items)},
        "gates": {"approved": approvals, "rejected": rejections,
                  "rework_rate": round(rejections / (approvals + rejections), 2) if approvals + rejections else None,
                  "avg_wait_min": avg(waits)},
        "tasks": {"total": len(tasks), "done": sum(t["status"] == "done" for t in tasks),
                  "first_try": sum(t["status"] == "done" and t["attempts"] == 1 for t in tasks),
                  "blocked": sum(t["status"] == "blocked" for t in tasks)},
        "agents": {"runs": sum(e["event"] == "agent_start" for e in evs),
                   "tool_calls": sum(e["event"] == "tool_use" for e in evs)},
        "by_type": by_type,
        "stage_minutes": [{"stage": s.name, "title": s.title, "avg": avg(per_stage[s.name]),
                           "count": len(per_stage[s.name])} for s in STAGES],
    }


def create_app(root: Path) -> FastAPI:
    app = FastAPI(title="Agentic SDLC dashboard")

    def cfg() -> dict[str, Any]:
        return config.load(root)

    runner = Runner(root, cfg)
    app.state.runner = runner

    def guard(fn, *args):
        try:
            return fn(*args)
        except (state.StateError, release.ReleaseError, gitops.GitError) as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/mode")
    def mode() -> dict[str, Any]:
        return {"mode": "project"}

    @app.get("/api/project")
    def project() -> dict[str, Any]:
        c = cfg()
        return {"root": str(root), "project": c["project"], "stack": c["stack"],
                "stages": [{"name": s.name, "title": s.title, "purpose": s.purpose, "automated": s.automated,
                            "agents": [st.agent for st in s.steps], **c["stages"].get(s.name, {})} for s in STAGES],
                "work_types": [{k: v for k, v in config.work_type(c, n).items() if k != "steps"}
                               | {"agents": {st: [a.agent for a in steps] for st, steps in WORK_TYPES[n].steps.items()}}
                               for n in WORK_TYPES]}

    def person(request: Request) -> None:
        """Human actions come from the dashboard page in a browser: its per-launch token and a same-origin
        request. An agent calling the API with curl gets 403, under any AI tool."""
        if not human.from_browser({k.lower(): v for k, v in request.headers.items()}):
            raise HTTPException(403, "This is a human decision: use the dashboard page in your browser, "
                                     "or `sdlc ...` in a terminal.")

    def running() -> set[str]:
        """Items with agents at work: started from the dashboard, or seen working through the hooks (e.g. a run
        started in a terminal), so their tasks are not mistaken for abandoned ones."""
        return ({r["req"] for r in runner.list() if r["status"] == "running"}
                | {a["req"] for a in live_agents(events.read(root)) if a.get("req")})

    @app.get("/api/home")
    def home() -> dict[str, Any]:
        """The welcome page: who is here, the agent team and the last unfinished job."""
        c = cfg()
        plugin = plugin_dir()
        return {"user": _who(), "project": c["project"]["name"],
                "team": assistant.team(c, live_agents(events.read(root)), plugin),
                "last_job": assistant.last_job(root, c, running()),
                "work_types": [{"name": n, "title": config.work_type(c, n)["title"],
                                "purpose": config.work_type(c, n)["purpose"], "plan": assistant.plan(c, n)}
                               for n in WORK_TYPES]}

    @app.get("/api/library")
    def library() -> dict[str, Any]:
        """Standards, skills and templates the agents use: whose each one is and who uses it."""
        c = cfg()
        return {"standards": standards.resolve(root, c), "templates": standards.templates(root),
                "found": [f for f in standards.detect(root)
                          if f["path"] not in {s["path"] for s in standards.resolve(root, c)}],
                "agents": standards.all_agents()}

    def lib(fn, *args):
        try:
            return fn(*args)
        except standards.LibraryError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/library/{kind}/download")
    def lib_download(kind: str, name: str) -> PlainTextResponse:
        entry = lib(standards.locate, root, cfg(), kind, name)
        p = root / entry["path"]
        if not p.is_file():
            raise HTTPException(404, f"{entry['path']} is missing")
        return PlainTextResponse(p.read_text(), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{p.name}"'})

    def changed(what: str, kind: str, name: str, out: dict[str, Any]) -> dict[str, Any]:
        c = cfg()
        generate.write_claude_md(root, c)  # the standards list in CLAUDE.md follows
        events.emit(root, "library_changed", by=_who(), detail=f"{what} {kind} {name}: {out.get('path', '')}")
        return {"ok": True, **out, "entry": lib(standards.locate, root, c, kind, name) if what != "removed" else None}

    @app.post("/api/library/{kind}/{name:path}/reset", dependencies=[Depends(person)])
    def lib_reset(kind: str, name: str) -> dict[str, Any]:
        """A person puts the default back (the replaced version is kept in .sdlc/library-history/)."""
        return changed("reset", kind, name, lib(standards.reset, root, cfg(), kind, name))

    @app.post("/api/library/similar")
    def lib_similar(n: LibUpload, title: str = "") -> dict[str, Any]:
        """Does this upload look like a standard the project already has? (then replace it, don't add it)"""
        return {"match": standards.similar(root, cfg(), title, n.content)}

    @app.post("/api/library/standard", dependencies=[Depends(person)])
    def lib_add(n: LibNew) -> dict[str, Any]:
        """A person adds the team's own standard or skill and picks the agents that follow it."""
        return changed("added", "standard", n.name,
                       lib(standards.add, root, n.name, n.title, n.agents, n.content, n.skill, n.filename))

    @app.delete("/api/library/standard/{name}", dependencies=[Depends(person)])
    def lib_remove(name: str) -> dict[str, Any]:
        return changed("removed", "standard", name, lib(standards.remove, root, name))

    @app.post("/api/library/{kind}/{name:path}", dependencies=[Depends(person)])
    def lib_replace(kind: str, name: str, u: LibUpload) -> dict[str, Any]:
        """A person uploads a new version of a standard, skill or template."""
        return changed("replaced", kind, name, lib(standards.replace, root, cfg(), kind, name, u.content, u.filename))

    @app.post("/api/home/classify")
    def classify(a: Ask) -> dict[str, Any]:
        if not a.text.strip():
            raise HTTPException(422, "Tell me what you need")
        return assistant.classify(a.text, state.load_all(root), cfg())

    @app.post("/api/home/start", dependencies=[Depends(person)])
    def start_work(w: NewWork) -> dict[str, Any]:
        """A person confirms the suggested work item: it is created and, if asked, the agents start on it."""
        if w.type not in WORK_TYPES:
            raise HTTPException(422, f"type must be one of {', '.join(WORK_TYPES)}")
        if not w.title.strip():
            raise HTTPException(422, "Give the work a title")
        if any(x.get("rel") not in ("updates", "fixes", "blocked_by") or not x.get("req") for x in w.links):
            raise HTTPException(422, "links: rel updates, fixes or blocked_by, and a req")
        made = guard(new_work, root, cfg(), w.title.strip(), w.type, w.links, w.text, w.by or _who())
        events.emit(root, "item_requested", req=made["req"], by=w.by or _who(), detail=f"{w.type} from the dashboard: {w.title}")
        run = None
        held = blockers.blocking(guard(blockers.find, root, cfg(), made["req"]))
        if w.run and held:  # don't start a run that would stop at once: the person sees the checklist instead
            return {**made, "run": None, "blockers": held}
        if w.run:
            try:
                run = runner.start(made["req"]).public()
            except RuntimeError as e:
                return {**made, "run": None, "run_error": str(e)}
        return {**made, "run": run}

    @app.get("/api/items")
    def items() -> list[dict[str, Any]]:
        return state.load_all(root)

    @app.get("/api/items/{req}")
    def item(req: str) -> dict[str, Any]:
        it = guard(state.load, root, req)
        reviews = {}
        for t in it["tasks"]:
            p = root / ".sdlc" / "reviews" / f"{req}-{t['id']}.md"
            if p.exists():
                first = p.read_text().splitlines()[:1]
                reviews[t["id"]] = first[0].removeprefix("Verdict:").strip() if first else ""
        run = runner.active(req)
        last = next((r for r in runner.list() if r["req"] == req), None)  # newest first
        latest = last  # for "View last run log", whatever stage it was in
        cur_start = (it["stages"].get(it["current_stage"] or "", {}) or {}).get("started") or ""
        if last and (not cur_start or last["started"] < cur_start):
            last = None  # that run was for an earlier stage: don't show it as this stage's
        c = cfg()
        wt = config.work_type(c, state.item_type(it))
        return {**it, "type": wt["name"], "reviews": reviews, "pr": it.get("pr"), "deployments": it.get("deployments", []),
                "smoke": it.get("smoke", []), "next": state.next_action(root, c, req),
                "run": run.public() if run else None, "stage_titles": wt["stage_titles"],
                "can_retype": it["stages"]["development"]["status"] in (state.PENDING, state.SKIPPED),
                "flow": assistant.item_flow(root, c, it, events.read(root)), "user": _who(),
                "last_run": last if last and last["status"] != "running" else None,
                "latest_run": latest,
                "blockers": guard(blockers.find, root, c, req, req in running()),
                "agents_active": req in running(),
                "go_live": release.go_live(c, it, root)}

    @app.get("/api/items/{req}/live")
    def item_live(req: str, stage: str | None = None) -> dict[str, Any]:
        """The control-room view of one stage: orchestrator, lead, sub-agents and their handoffs."""
        it = guard(state.load, root, req)
        return assistant.live_view(root, cfg(), it, events.read(root), stage, req in running())

    @app.get("/api/go-live")
    def ready_to_go_live() -> list[dict[str, Any]]:
        """Items whose Release is approved and that a person still has to take live."""
        c = cfg()
        out = []
        for it in state.load_all(root):
            g = release.go_live(c, it, root)
            if g and g["state"] == "ready":
                out.append({"req": it["id"], "title": it["title"], "type": state.item_type(it), **g})
        return out

    class WentLive(BaseModel):
        version: str = ""
        note: str = ""

    @app.post("/api/items/{req}/went-live", dependencies=[Depends(person)])
    def went_live(req: str, w: WentLive) -> dict[str, Any]:
        """A person records that they deployed the item to production (agents never do this)."""
        it = guard(state.load, root, req)
        g = release.go_live(cfg(), it, root)
        if not g:
            raise HTTPException(409, "Release is not approved yet")
        msg = act("deployed", req, {"env": g["env"], "version": w.version or g["version"] or "", "note": w.note}, None)
        return {"ok": True, "message": msg, "go_live": release.go_live(cfg(), state.load(root, req), root)}

    @app.post("/api/items/{req}/help/explain", dependencies=[Depends(person)])
    async def explain_help(req: str) -> dict[str, Any]:
        """A person asks for plain-language next steps for what the last run asked (a quick AI call)."""
        r = await asyncio.to_thread(guard, blockers.explain_help, root, req)
        events.emit(root, "help_explained", req=req, by=_who(),
                    detail="next steps by AI" + (f" · ${r['cost_usd']:.2f}" if isinstance(r.get("cost_usd"), (int, float)) else ""))
        return r

    @app.post("/api/items/{req}/decision", dependencies=[Depends(person)])
    def decision(req: str, a: Answer) -> dict[str, Any]:
        """A person answers the agents' question (accept, or say what to do instead); it is written to the spec."""
        guard(state.load, root, req)
        return guard(blockers.record_decision, root, req, a.accepted, a.note, _who())

    @app.post("/api/items/{req}/help-handled", dependencies=[Depends(person)])
    def help_handled(req: str) -> dict[str, Any]:
        """A person says the last run's request for help is dealt with (the note goes away)."""
        events.emit(root, "help_handled", req=req, by=_who(), detail="marked as handled")
        return {"ok": True}

    @app.post("/api/items/{req}/fix-commits", dependencies=[Depends(person)])
    def fix_commits(req: str) -> dict[str, Any]:
        if runner.active(req):
            raise HTTPException(409, "Agents are running for this item; stop them first")
        r = guard(blockers.fix_commits, root, req)
        events.emit(root, "commits_fixed", req=req, by=_who(),
                    detail=f"dropped {', '.join(r['dropped']) or 'none'}; reworded {', '.join(r['reworded']) or 'none'}")
        return r

    @app.post("/api/items/{req}/reset-tasks", dependencies=[Depends(person)])
    def reset_tasks(req: str) -> dict[str, Any]:
        if runner.active(req):
            raise HTTPException(409, "Agents are running for this item; stop them first")
        r = guard(blockers.reset_tasks, root, req)
        events.emit(root, "tasks_reset", req=req, by=_who(), detail=", ".join(r["reset"]) or "none")
        return r

    @app.post("/api/items/{req}/free-worktrees", dependencies=[Depends(person)])
    def free_worktrees(req: str) -> dict[str, Any]:
        if runner.active(req):
            raise HTTPException(409, "Agents are running for this item; stop them first")
        r = guard(blockers.free_worktrees, root, req)
        events.emit(root, "worktrees_freed", req=req, by=_who(), detail=", ".join(r["freed"]) or "none")
        return r

    @app.get("/api/blockers")
    def all_blockers() -> list[dict[str, Any]]:
        """Every open item that needs a person before its agents can start (for "Waiting for you")."""
        return blockers.all_items(root, cfg(), running())

    @app.get("/api/items/{req}/spec")
    def spec_form(req: str) -> dict[str, Any]:
        return blockers.spec_form(root, guard(state.load, root, req))

    @app.post("/api/items/{req}/spec/suggest", dependencies=[Depends(person)])
    async def suggest_spec(req: str) -> dict[str, Any]:
        """A person asks for an AI draft of the spec sections (one short, read-only Claude Code call)."""
        it = guard(state.load, root, req)
        r = await asyncio.to_thread(guard, blockers.suggest_spec, root, cfg(), it)
        events.emit(root, "spec_suggested", req=req, by=_who(), detail=f"draft by {r['model']}"
                    + (f" · ${r['cost_usd']:.2f}" if isinstance(r.get("cost_usd"), (int, float)) else ""))
        return r

    @app.post("/api/items/{req}/spec", dependencies=[Depends(person)])
    def save_spec(req: str, s: SpecValues) -> dict[str, Any]:
        """A person fills in the spec sections the agents need (a task's What and why, Done when, ...)."""
        it = guard(state.load, root, req)
        p = guard(blockers.write_spec, root, it, s.values)
        events.emit(root, "spec_written", req=req, by=_who(), detail=f"{', '.join(s.values)} in {p.relative_to(root)}")
        return blockers.spec_form(root, state.load(root, req))

    class Switch(BaseModel):
        target: str

    @app.post("/api/git/switch", dependencies=[Depends(person)])
    def switch_branch(s: Switch) -> dict[str, Any]:
        r = guard(blockers.switch, root, s.target)
        events.emit(root, "branch_switched", by=_who(), detail=f"{r['from']} → {r['branch']}")
        return r

    @app.post("/api/git/commit", dependencies=[Depends(person)])
    def commit_setup(cm: CommitSetup) -> dict[str, Any]:
        """A person commits project-setup files, by default to the base branch (this folder then switches to it)."""
        r = guard(blockers.commit_setup, root, cfg(), cm.files, cm.message, cm.target)
        events.emit(root, "setup_committed", by=_who(), detail=f"{len(r['files'])} files to {r['branch']} at {r['commit']}")
        return r

    @app.get("/api/events")
    def get_events(limit: int = Query(200, le=5000), req: str | None = None) -> list[dict[str, Any]]:
        return events.read(root, limit=limit, req=req)

    @app.get("/api/live")
    def live() -> list[dict[str, Any]]:
        return live_agents(events.read(root))

    @app.get("/api/metrics")
    def get_metrics() -> dict[str, Any]:
        evs = events.read(root)
        return {**metrics(state.load_all(root), evs), "ai": usage.summarize(evs, cfg())}

    @app.get("/api/usage")
    def get_usage(req: str | None = None) -> dict[str, Any]:
        return usage.summarize(events.read(root), cfg(), req)

    @app.get("/api/file")
    def read_file(path: str) -> dict[str, Any]:
        p = (root / path).resolve()
        if not p.is_relative_to(root) or p.suffix not in READABLE or ".git" in p.parts:
            raise HTTPException(403, "Only text files inside the repo can be opened")
        if not p.is_file():
            raise HTTPException(404, f"{path} not found")
        if p.stat().st_size > MAX_FILE:
            raise HTTPException(413, f"{path} is larger than 512 KB")
        return {"path": path, "content": p.read_text(errors="replace")}

    def act(action: str, req: str, args: dict[str, Any], by: str | None) -> str:
        return guard(run_human, root, cfg(), action, req, args, by or _who(), "dashboard")

    @app.post("/api/items/{req}/approve", dependencies=[Depends(person)])
    def approve(req: str, d: Decision) -> dict[str, Any]:
        act("approve", req, {"comment": d.comment}, d.by)
        return {"ok": True, "next_stage": state.load(root, req)["current_stage"]}

    @app.post("/api/items/{req}/reject", dependencies=[Depends(person)])
    def reject(req: str, d: Decision) -> dict[str, Any]:
        if not d.comment.strip():
            raise HTTPException(422, "Say what the agents should change")
        act("reject", req, {"comment": d.comment}, d.by)
        return {"ok": True}

    @app.post("/api/items/{req}/defects/{did}", dependencies=[Depends(person)])
    def decide_defect(req: str, did: str, d: DefectDecision) -> dict[str, Any]:
        """A person defers, accepts or reopens a defect (agents cannot defer or accept)."""
        if d.action not in ("defer", "accept", "reopen"):
            raise HTTPException(422, "action must be defer, accept or reopen")
        if not d.comment.strip():
            raise HTTPException(422, "Say why")
        if d.action == "reopen":
            return {"ok": True, "defect": guard(defect_item, root, req, did, "reopen", d.by or _who(), d.comment)}
        act("defect-decision", req, {"defect": did, "decision": d.action, "reason": d.comment}, d.by)
        return {"ok": True, "defect": next(x for x in state.load(root, req)["defects"] if x["id"] == did)}

    @app.post("/api/items/{req}/retype", dependencies=[Depends(person)])
    def retype(req: str, r: Retype) -> dict[str, Any]:
        """A person changes the kind of work; the item restarts at the new type's first stage."""
        if not r.comment.strip():
            raise HTTPException(422, "Say why the type changes")
        if runner.active(req):
            raise HTTPException(409, "Agents are running for this item; stop them first")
        act("retype", req, {"type": r.type, "reason": r.comment}, r.by)
        return {"ok": True, "next_stage": state.load(root, req)["current_stage"]}

    @app.post("/api/items/{req}/tasks/{task}/unblock", dependencies=[Depends(person)])
    def unblock(req: str, task: str) -> dict[str, Any]:
        act("unblock", req, {"task": task}, None)
        return {"ok": True, "status": next(t for t in state.load(root, req)["tasks"] if t["id"] == task)["status"]}

    @app.get("/api/pending")
    def pending() -> list[dict[str, Any]]:
        return human.pending(root)

    @app.post("/api/pending/{rid}/confirm", dependencies=[Depends(person)])
    def confirm(rid: str, d: Decision) -> dict[str, Any]:
        """A person confirms a request an agent or a script filed (the action runs now, as this person)."""
        try:
            p = human.take(root, rid)
        except KeyError as e:
            raise HTTPException(404, f"No pending request {rid}") from e
        try:
            return {"ok": True, "message": act(p["action"], p["req"], p["args"], d.by)}
        except HTTPException:
            human.request(root, p["action"], p["req"], p["args"], p["summary"])  # keep it pending if it failed
            raise

    @app.post("/api/pending/{rid}/dismiss", dependencies=[Depends(person)])
    def dismiss(rid: str) -> dict[str, Any]:
        try:
            human.take(root, rid)
        except KeyError as e:
            raise HTTPException(404, f"No pending request {rid}") from e
        return {"ok": True}

    @app.post("/api/items/{req}/run", dependencies=[Depends(person)])
    def run_agents(req: str, force: bool = False) -> dict[str, Any]:
        """Start /sdlc-run for this item: the agents work until the next gate. Held back while a person still
        has something to fix first (force=true runs anyway)."""
        nxt = guard(state.next_action, root, cfg(), req)
        if nxt["action"] not in ("run_stage", "complete_stage"):
            raise HTTPException(409, nxt.get("message") or f"Nothing for agents to do ({nxt['action']})")
        if not force and (found := blockers.blocking(guard(blockers.find, root, cfg(), req))):
            raise HTTPException(409, f"{len(found)} thing{'s' * (len(found) != 1)} need you first: "
                                     + "; ".join(b["title"] for b in found))
        try:
            return runner.start(req).public()
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/claude/auth")
    async def claude_auth() -> dict[str, Any]:
        return await asyncio.to_thread(auth_status)

    @app.post("/api/claude/login", dependencies=[Depends(person)])
    def claude_login() -> dict[str, Any]:
        """A person signs Claude Code in again (the sign-in page opens in their browser)."""
        try:
            return start_login()
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        return runner.list()

    @app.get("/api/runs/{run_id}/log")
    def run_log(run_id: str) -> dict[str, Any]:
        try:
            return {"run": runner.runs[run_id].public(), "text": runner.tail(run_id)}
        except KeyError as e:
            raise HTTPException(404, f"No run {run_id}") from e

    @app.post("/api/runs/{run_id}/stop", dependencies=[Depends(person)])
    def stop_run(run_id: str) -> dict[str, Any]:
        try:
            return runner.stop(run_id).public()
        except KeyError as e:
            raise HTTPException(404, f"No run {run_id}") from e

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        """Server-sent events: one `data:` line per new line in events.jsonl."""
        path = root / events.EVENTS_FILE

        async def gen():
            pos = path.stat().st_size if path.exists() else 0
            yield "event: hello\ndata: {}\n\n"
            idle = 0
            while not await request.is_disconnected():
                size = path.stat().st_size if path.exists() else 0
                if size < pos:  # file was truncated or replaced
                    pos = 0
                if size > pos:
                    with open(path, "rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                    # only consume whole lines; a partial last line is picked up next tick
                    whole = chunk[: chunk.rfind(b"\n") + 1]
                    pos += len(whole)
                    for line in whole.decode(errors="replace").splitlines():
                        if line.strip():
                            yield f"data: {line}\n\n"
                    idle = 0
                else:
                    idle += 1
                    if idle % 30 == 0:
                        yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> HTMLResponse:
            page = (STATIC / "index.html").read_text()  # the per-launch token proves actions come from this page
            return HTMLResponse(page.replace("<head>", f'<head><meta name="sdlc-human" content="{human.dashboard_token()}">', 1),
                                headers={"Cache-Control": "no-store"})

    return app


def create_portfolio_app(paths: list[Path]) -> FastAPI:
    """Read-only dashboard across projects: the same UI, in portfolio mode."""
    from .. import portfolio

    app = FastAPI(title="Agentic SDLC portfolio")

    @app.get("/api/mode")
    def mode() -> dict[str, Any]:
        return {"mode": "portfolio"}

    @app.get("/api/portfolio")
    def get_portfolio() -> dict[str, Any]:
        return portfolio.collect(paths)

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> HTMLResponse:
            return HTMLResponse((STATIC / "index.html").read_text(), headers={"Cache-Control": "no-store"})

    return app


def serve_portfolio(paths: list[Path], host: str = "127.0.0.1", port: int = 8770) -> None:
    import uvicorn

    uvicorn.run(create_portfolio_app(paths), host=host, port=port, log_level="warning")


def serve(root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(create_app(root), host=host, port=port, log_level="warning")

