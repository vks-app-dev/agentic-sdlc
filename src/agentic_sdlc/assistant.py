"""The dashboard's home page: the agent team, the last unfinished job, and turning a request into a work item.

`classify` is deliberately simple and free: keyword rules and word overlap with existing items. Its answer is
only a suggestion; the person sees it and can change the type, title and links before any agent starts.
"""

import re
from pathlib import Path
from typing import Any

from . import config, state
from .stages import BY_NAME, STAGES, WORK_TYPES

LEADS = {"requirements": "Requirements Lead", "design": "Design Architect", "planning": "Planning Lead",
         "development": "Development Lead", "cicd": "CI/CD Engineer", "qa": "QA Lead", "release": "Release Manager",
         "production": "Production (you)", "monitoring": "Monitoring"}

_SIGNALS = {
    "bug": (2, r"bugs?|fix(es|ed)?|broken|errors?|crash(es|ed)?|fail(s|ed|ing)?|wrong|incorrect|not working|"
               r"doesn'?t work|does not|can'?t|cannot|exceptions?|regression|500|typo"),
    "task": (2, r"refactor(ing)?|upgrade|bump|dependenc(y|ies)|ci|pipeline|lint(ing)?|tooling|clean ?up|chore|"
                r"tech(nical)? debt|docs?|documentation|readme|migrate to|rename"),
    "change": (1, r"change[sd]?|modify|update[sd]?|edit(ed|able|ing)?|instead( of)?|rather than|no longer|"
                  r"should now|extend|improve|adjust|allow .* to (edit|change|update|delete)"),
    "feature": (1, r"add|new|allow|support|let (users|me|people)|ability|create|build|implement|introduce|enable"),
}
_STOP = set("a an the to of for and or in on at by with from is are be can should could would will i we you "
            "it this that as so do does not no user users want need please new add allow let make after before "
            "they them their when there been has have its all any some into than then".split())
_LEADING = re.compile(r"^(please|can you|could you|i want( you)? to|i'?d like( you)? to|we need to|we want to|"
                      r"help me|let'?s)\s+", re.I)


def _stem(w: str) -> str:
    for end in ("ing", "ed", "s"):
        if w.endswith(end) and len(w) - len(end) >= 3:
            return w[: -len(end)]
    return w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z][a-z0-9]+", text.lower()) if w not in _STOP}


def title_from(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s|\n", text.strip(), maxsplit=1)[0].rstrip(".!? ")
    first = _LEADING.sub("", first).strip()
    first = first[:1].upper() + first[1:]
    return first if len(first) <= 80 else first[:77].rsplit(" ", 1)[0] + "…"


def classify(text: str, items: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    low = text.lower()
    scores, why = {}, {}
    for wtype, (weight, pattern) in _SIGNALS.items():
        hits = sorted({m.group(0) for m in re.finditer(rf"\b({pattern})\b", low)})
        scores[wtype], why[wtype] = weight * len(hits), hits
    words = _words(text)
    related = []
    for it in items:
        shared = words & _words(it["title"])
        if shared:
            related.append({"req": it["id"], "title": it["title"], "shared": sorted(shared),
                            "score": round(len(shared) / len(words | _words(it["title"])), 2)})
    related.sort(key=lambda r: -r["score"])
    best = related[0] if related and related[0]["score"] >= 0.2 else None
    wtype = max(("bug", "task", "change", "feature"), key=lambda t: scores[t]) if any(scores.values()) else "feature"
    if wtype == "change" and not best:  # a change needs something existing to change
        wtype = "feature" if scores["feature"] else "change"
    reasons = [f"mentions {', '.join(repr(h) for h in why[wtype])}"] if why[wtype] else ["no strong signal: a feature by default"]
    links = []
    if best and wtype in ("change", "bug"):
        rel = "updates" if wtype == "change" else "fixes"
        links.append({"rel": rel, "req": best["req"]})
        reasons.append(f"looks related to {best['req']} ({best['title']}): shares {', '.join(best['shared'])}")
    total = sum(scores.values()) or 1
    wt = config.work_type(cfg, wtype)
    return {"type": wtype, "type_title": wt["title"], "purpose": wt["purpose"], "title": title_from(text),
            "links": links, "related": related[:3], "reasons": reasons,
            "confidence": "high" if scores[wtype] / total >= 0.6 and scores[wtype] >= 2 else "medium" if scores[wtype] else "low",
            "plan": plan(cfg, wtype), "plans": {t: plan(cfg, t) for t in WORK_TYPES},
            "types": {t: config.work_type(cfg, t)["purpose"] for t in WORK_TYPES}}


def plan(cfg: dict[str, Any], wtype: str) -> list[dict[str, Any]]:
    """The stages this kind of work goes through here, with the agents of each and who approves."""
    wt = config.work_type(cfg, wtype)
    out = []
    for s in STAGES:
        sc = cfg["stages"].get(s.name, {})
        if not sc.get("enabled", True) or (s.name in ("requirements", "design", "planning", "development")
                                           and s.name not in wt["stages"]):
            continue
        steps = wt["steps"].get(s.name, s.steps)
        gate = wt["gates"].get(s.name, sc.get("gate", list(s.default_gate)))
        out.append({"stage": s.name, "title": wt["stage_titles"].get(s.name, s.title),
                    "agents": [st.agent for st in steps], "gate": gate, "automated": s.automated,
                    "note": "size M or L only" if s.name in ("design", "planning") and wt["design_if_size"] else ""})
    return out


def _descriptions(plugin_dir: Path | None) -> dict[str, str]:
    out = {}
    for p in (plugin_dir / "agents").glob("*.md") if plugin_dir and (plugin_dir / "agents").exists() else []:
        m = re.search(r"^description:\s*(.+)$", p.read_text(), re.M)
        if m:
            d = re.sub(r"^Agentic SDLC [\w/ ]+? stage\.\s*", "", m.group(1)).split(" Use for ")[0]
            out[p.stem] = d.strip()
    return out


def team(cfg: dict[str, Any], live: list[dict[str, Any]], plugin_dir: Path | None = None) -> list[dict[str, Any]]:
    """One lead per stage, with its sub-agents in the order they run (same step = at the same time)."""
    desc = _descriptions(plugin_dir)
    busy = {(a.get("agent") or "").split(":")[-1] for a in live}
    out = []
    for s in STAGES:
        sc = cfg["stages"].get(s.name, {})
        agents: dict[str, dict[str, Any]] = {}
        variants = [("", s.steps)] + [(w.name, w.steps[s.name]) for w in WORK_TYPES.values() if s.name in w.steps]
        for for_type, steps in variants:
            groups = sorted({st.parallel_group for st in steps})
            for st in steps:
                a = agents.setdefault(st.agent, {
                    "name": st.agent, "does": st.does, "output": st.output, "about": desc.get(st.agent, ""),
                    "model": config.agent_model(cfg, st.agent), "step": groups.index(st.parallel_group) + 1,
                    "when": "only when the item has a UI" if st.when == "ui" else "", "for": []})
                if for_type:
                    a["for"].append(WORK_TYPES[for_type].title.lower())
        for a in agents.values():
            if any(st.agent == a["name"] for st in s.steps):
                a["for"] = []  # a standard agent of the stage, whatever the work type
            a["status"] = "working" if a["name"] in busy else "ready"
        enabled = sc.get("enabled", True)
        status = ("human" if s.name == "production" else "working" if any(a["status"] == "working" for a in agents.values())
                  else "ready" if enabled and s.automated else "off")
        out.append({"stage": s.name, "title": s.title, "lead": LEADS.get(s.name, s.title), "purpose": s.purpose,
                    "status": status, "enabled": enabled, "gate": sc.get("gate", list(s.default_gate)),
                    "agents": sorted(agents.values(), key=lambda a: (bool(a["for"]), a["step"]))})
    return out


def last_job(root: Path, cfg: dict[str, Any], running: set[str], paused: set[str] = frozenset()) -> dict[str, Any] | None:
    """The most recently touched unfinished item and what it needs next."""
    open_items = sorted((i for i in state.load_all(root) if i["status"] != "done"), key=lambda i: i["updated"],
                        reverse=True)
    if not open_items:
        return None
    it = open_items[0]
    nxt = state.next_action(root, cfg, it["id"])
    stage = it["current_stage"]
    wt = config.work_type(cfg, state.item_type(it))
    from . import blockers

    held = blockers.blocking(blockers.find(root, cfg, it["id"])) if it["id"] not in running else []
    can = nxt["action"] in ("run_stage", "complete_stage") and not held
    who = ("agents" if it["id"] in running else "agents" if can else "you" if held or nxt["action"] in
           ("await_gate", "human_needed", "human_deploy", "manual") else "waiting")
    title = wt["stage_titles"].get(stage, BY_NAME[stage].title) if stage else "Done"
    message = (f"{len(held)} thing{'s' * (len(held) != 1)} need you before the agents can start: "
               + "; ".join(b["title"][:1].lower() + b["title"][1:] for b in held) + ".") if held else nxt.get("message") or (
        f"Agents are working on {title} now." if it["id"] in running else
        f"{title} is {it['stages'][stage]['status'].replace('_', ' ')}: the agents can carry on from where they stopped."
        if can else "")
    return {"req": it["id"], "title": it["title"], "type": state.item_type(it), "updated": it["updated"],
            "stage": stage, "stage_title": title, "action": nxt["action"], "message": message, "can_resume": can and it["id"] not in running,
            "running": it["id"] in running, "turn": who, "others": len(open_items) - 1,
            "paused": it["id"] in paused and it["id"] not in running}


# Design: which agent writes which workstream file. The architect ticks the workstreams an item needs in the
# design README; an agent whose files are all unticked is not run for that item.
WORKSTREAM_AGENTS = {"data": "design-data-api-designer", "api": "design-data-api-designer", "ux": "design-ux-designer",
                     "operations": "design-ops-designer", "lld": "design-detail-designer",
                     "security": "design-security-reviewer"}


def design_choices(root: Path, req: str) -> dict[str, str]:
    """agent -> why it is not needed, from the architect's unticked workstreams (empty before the architect decides)."""
    p = root / "docs" / "design" / req / "README.md"
    if not p.exists():
        return {}
    ticked, unticked = set(), {}
    for m in re.finditer(r"^- \[( |x|X)\] (\w+)\.md:?\s*(.*)$", p.read_text(), re.M):
        name = m.group(2)
        if name not in WORKSTREAM_AGENTS:
            continue
        if m.group(1).strip():
            ticked.add(WORKSTREAM_AGENTS[name])
        else:
            why = re.search(r"\(([^)]*)\)\s*$", m.group(3))
            unticked[WORKSTREAM_AGENTS[name]] = (why.group(1) if why else m.group(3)).strip() or "not needed"
    if not ticked:  # nothing decided yet (the template is all unticked)
        return {}
    return {a: why for a, why in unticked.items() if a not in ticked}


def item_flow(root: Path, cfg: dict[str, Any], item: dict[str, Any], evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What this item goes through (the stages that run for it, their agents in order, the human reviews) and
    what actually happened: each stage's status and, per agent, done / working / waiting from the hook events.
    Agents that ran but are not in the plan are listed too, so planned and actual can be compared."""
    req = item["id"]
    runs: dict[str, dict[str, Any]] = {}  # agent_id -> {agent, start, stop}
    for e in evs:
        if e["event"] == "run_finished" and e.get("req") == req:  # the run ended: nothing of it still works
            for r in runs.values():
                r["stop"] = r["stop"] or e["ts"]
            continue
        aid = e.get("agent_id")
        if not aid:
            continue
        if e["event"] == "agent_start" and e.get("req") == req:
            runs[aid] = {"agent": (e.get("agent") or "").split(":")[-1], "start": e["ts"], "stop": None,
                         "task": e.get("task")}
        elif e["event"] == "agent_stop" and aid in runs:
            runs[aid]["stop"] = e["ts"]
    wt = config.work_type(cfg, state.item_type(item))
    ui = state.has_ui(root, item, cfg)
    out = []
    for s in STAGES:
        st = item["stages"][s.name]
        if st["status"] == state.SKIPPED or not state.runs(item, cfg, s.name):
            continue
        since = st.get("started") or ""
        mine = [r for r in runs.values() if r["start"] >= since] if since else []
        steps = [x for x in wt["steps"].get(s.name, s.steps) if x.when != "ui" or ui]
        groups = sorted({x.parallel_group for x in steps})
        finished = st["status"] in (state.APPROVED, state.AWAITING)

        def status(agent: str) -> tuple[str, int]:
            seen = [r for r in mine if r["agent"] == agent]
            if any(r["stop"] is None for r in seen):
                return "working", len(seen)
            if seen or finished:
                return "done", len(seen)
            return "waiting", 0

        not_needed = design_choices(root, req) if s.name == "design" else {}
        agents = []
        for x in steps:
            sts, n = status(x.agent)
            row = {"name": x.agent, "step": groups.index(x.parallel_group) + 1, "status": sts, "runs": n}
            if x.agent in not_needed and n == 0:  # the architect decided this item does not need it
                row.update(status="skipped", reason=f"Not needed: {not_needed[x.agent]}")
            agents.append(row)
        planned = {x.agent for x in steps}
        extra = sorted({r["agent"] for r in mine if r["agent"] and r["agent"] not in planned
                        and r["agent"].split("-")[0] == {"requirements": "req", "planning": "plan", "release": "rel",
                                                         "development": "dev"}.get(s.name, s.name)})
        out.append({"stage": s.name, "title": wt["stage_titles"].get(s.name, s.title), "status": st["status"],
                    "lead": LEADS.get(s.name, s.title), "human": s.name == "production",
                    "automated": s.automated, "gate": st.get("gate") or [],
                    "decided_by": (st.get("decision") or {}).get("by"),
                    "agents": agents, "extra": [{"name": a, "status": status(a)[0], "runs": status(a)[1]} for a in extra]})
    return out


_PREFIX = {"req": "requirements", "design": "design", "plan": "planning", "dev": "development", "qa": "qa",
           "rel": "release"}


def live_view(root: Path, cfg: dict[str, Any], item: dict[str, Any], evs: list[dict[str, Any]],
              stage: str | None = None, running: bool = False) -> dict[str, Any]:
    """One stage as a control room: the orchestrator, the stage lead, every planned sub-agent with what it is doing
    (or did), and the handoffs between them in order, from the hook events."""
    from datetime import datetime

    req = item["id"]
    flow = item_flow(root, cfg, item, evs)
    stages = [f["stage"] for f in flow]
    if not stage or stage not in stages:
        stage = item["current_stage"] if item["current_stage"] in stages else (stages[-1] if stages else None)
    if not stage:
        return {"stage": None, "stages": stages}
    st = item["stages"][stage]
    start, end = st.get("started") or "", st.get("ended") or ""
    in_window = lambda ts: (not start or ts >= start) and (not end or ts <= end)  # noqa: E731
    mine = [e for e in evs if e.get("req") == req and in_window(e["ts"])]

    def secs(a: str, b: str | None) -> int | None:
        try:
            return int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()) if b else None
        except ValueError:
            return None

    runs: dict[str, dict[str, Any]] = {}
    orch = {"status": "working" if running else "idle", "last": None, "last_ts": None, "actions": 0}
    handoffs: list[dict[str, Any]] = []
    for e in mine:
        name = (e.get("agent") or "").split(":")[-1]
        aid = e.get("agent_id")
        if e["event"] == "agent_start" and aid:
            runs[aid] = {"id": aid, "agent": name, "task": e.get("task"), "start": e["ts"], "stop": None,
                         "actions": 0, "last": None, "last_ts": e["ts"], "result": None}
            handoffs.append({"ts": e["ts"], "kind": "dispatch", "agent": name, "text": e.get("task") or "started"})
        elif e["event"] == "agent_stop" and aid in runs:
            r = runs[aid]
            r.update(stop=e["ts"], result=(e.get("detail") or "")[:240])
            handoffs.append({"ts": e["ts"], "kind": "done", "agent": name, "text": (e.get("detail") or "finished")[:160],
                             "took": secs(r["start"], e["ts"])})
        elif e["event"] == "tool_use":
            act = f"{e.get('tool', '')}: {e.get('detail', '')}".strip(": ")
            if aid and aid in runs:
                runs[aid].update(actions=runs[aid]["actions"] + 1, last=act, last_ts=e["ts"])
            elif name == "orchestrator":
                orch.update(last=act, last_ts=e["ts"], actions=orch["actions"] + 1)
                if str(e.get("detail", "")).startswith("sdlc ") and not str(e.get("detail")).startswith(("sdlc next", "sdlc status")):
                    handoffs.append({"ts": e["ts"], "kind": "orchestrator", "agent": "orchestrator",
                                     "text": str(e.get("detail"))[:120]})
        elif e["event"] in ("gate_waiting", "gate_approved", "gate_rejected", "human_needed", "deployed", "smoke",
                            "run_started", "run_finished"):
            if e["event"] == "run_finished":  # the run ended: what it left open was interrupted
                for r in runs.values():
                    if r["stop"] is None:
                        r.update(stop=e["ts"], result=f"Interrupted: the run was {e.get('status') or 'stopped'}")
            handoffs.append({"ts": e["ts"], "kind": "human" if e["event"].startswith(("gate", "human")) else "engine",
                             "agent": e.get("by") or "engine", "text": e["event"].replace("_", " ")
                             + (f": {e.get('detail')}" if e.get("detail") else "")})
    fs = next(f for f in flow if f["stage"] == stage)
    agents = []
    for a in fs["agents"] + [{**x, "step": 0} for x in fs["extra"]]:
        rs = [r for r in runs.values() if r["agent"] == a["name"]]
        agents.append({**a, "planned": a in fs["agents"], "runs": [
            {**r, "took": secs(r["start"], r["stop"])} for r in rs]})
    return {"stage": stage, "stages": stages, "title": fs["title"], "lead": fs["lead"], "status": fs["status"],
            "gate": fs["gate"], "orchestrator": orch, "agents": agents, "handoffs": handoffs[-40:][::-1],
            "running": running}


def pause_info(evs: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    """What a paused run had done and what it cut off: sub-agents that finished, and those still working when the
    person paused (they run again on Resume)."""
    started, done, cut = None, [], {}
    for e in evs:
        if e.get("run") == run["id"] and e["event"] == "run_started":
            started = e["ts"]
            continue
        if started is None or e.get("req") != run["req"]:
            continue
        if e.get("run") == run["id"] and e["event"] == "run_finished":
            break
        name = (e.get("agent") or "").split(":")[-1]
        if e["event"] == "agent_start" and e.get("agent_id"):
            cut[e["agent_id"]] = {"name": name, "task": e.get("task")}
        elif e["event"] == "agent_stop" and e.get("agent_id") in cut:
            done.append(cut.pop(e["agent_id"]))
    return {"finished": done, "interrupted": list(cut.values()), "cost_usd": run.get("cost_usd"),
            "paused_at": run.get("ended")}
