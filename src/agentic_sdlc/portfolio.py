"""Many projects at once: `sdlc portfolio` reads each repo's .sdlc/ (local clones) and summarises them.

The projects come from the command line, or from `projects:` in the company's org.yaml. Read-only: nothing in
the repos is changed. `sdlc portfolio --serve` opens the dashboard in portfolio mode.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, events, state, usage


def _hours(a: str | None, b: str | None) -> float | None:
    if not (a and b):
        return None
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


def project(root: Path) -> dict[str, Any]:
    try:
        cfg = config.load(root)
    except (config.ConfigError, FileNotFoundError, OSError) as e:
        return {"path": str(root), "name": root.name, "error": str(e)[:300]}
    items = state.load_all(root)
    evs = events.read(root)
    now = datetime.now().astimezone()
    waiting, blocked, defects = [], [], []
    for it in items:
        cur = it["current_stage"]
        st = it["stages"].get(cur, {}) if cur else {}
        if st.get("status") == state.AWAITING:
            since = st.get("awaiting_since")
            waiting.append({"req": it["id"], "title": it["title"], "stage": cur, "gate": st.get("gate", []),
                            "since": since, "hours": round((now - datetime.fromisoformat(since)).total_seconds() / 3600, 1)
                            if since else None})
        if it["status"] == "blocked":
            blocked.append(it["id"])
        defects += [d for d in it.get("defects", []) if d["status"] in state.D_OPEN]
    done = [it for it in items if it["status"] == "done"]
    lead = [h for it in done if (h := _hours(it["created"], it["updated"])) is not None]
    approvals = sum(e["event"] == "gate_approved" for e in evs)
    rejections = sum(e["event"] == "gate_rejected" for e in evs)
    ai = usage.summarize(evs, cfg)
    by_stage: dict[str, int] = {}
    for it in items:
        if it["status"] != "done":
            by_stage[it["current_stage"] or "done"] = by_stage.get(it["current_stage"] or "done", 0) + 1
    return {
        "path": str(root), "name": cfg["project"]["name"], "org": (cfg.get("_org") or {}).get("name"),
        "requires": cfg.get("requires"),
        "items": {"total": len(items), "active": sum(i["status"] == "active" for i in items),
                  "blocked": len(blocked), "done": len(done)},
        "in_stage": by_stage,
        "waiting": sorted(waiting, key=lambda w: -(w["hours"] or 0)),
        "blocked": blocked,
        "defects_open": len(defects), "blockers_open": sum(d["blocker"] for d in defects),
        "lead_time_h": round(sum(lead) / len(lead), 1) if lead else None,
        "rework_rate": round(rejections / (approvals + rejections), 2) if approvals + rejections else None,
        "ai": {"tokens": ai["total"]["tokens"], "cost_usd": ai["total"]["cost_usd"],
               "per_done_item_usd": round(ai["total"]["cost_usd"] / len(done), 2) if done else None},
        "last_activity": evs[-1]["ts"] if evs else None,
    }


def collect(paths: list[Path]) -> dict[str, Any]:
    projects = [project(p) for p in paths]
    ok = [p for p in projects if "error" not in p]
    waiting = sorted(({**w, "project": p["name"]} for p in ok for w in p["waiting"]), key=lambda w: -(w["hours"] or 0))
    return {
        "projects": projects,
        "totals": {"projects": len(projects), "items_in_flight": sum(p["items"]["active"] + p["items"]["blocked"] for p in ok),
                   "gates_waiting": len(waiting), "blocked": sum(p["items"]["blocked"] for p in ok),
                   "blockers_open": sum(p["blockers_open"] for p in ok),
                   "ai_cost_usd": round(sum(p["ai"]["cost_usd"] for p in ok), 2),
                   "ai_tokens": sum(p["ai"]["tokens"] for p in ok)},
        "waiting": waiting,
    }


def paths_from(args: list[str], cwd: Path) -> list[Path]:
    """Explicit paths, else the org's `projects:` (org.yaml found from the current repo or SDLC_ORG_CONFIG)."""
    if args:
        return [Path(a).expanduser().resolve() for a in args]
    from . import org

    try:
        root = config.find_root(cwd)
        raw = config.load(root)
        listed = (raw.get("_org") or {}).get("projects") or []
    except config.ConfigError:
        p = org.locate(cwd, {})
        listed = org.apply({}, org.load(p))[0]["_org"]["projects"] if p else []
    if not listed:
        raise config.ConfigError("Name the projects (`sdlc portfolio ../a ../b`) or list them under `projects:` in org.yaml")
    return [Path(x) for x in listed]
