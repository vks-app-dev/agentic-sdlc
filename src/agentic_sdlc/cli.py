"""`sdlc` command line: the engine that slash commands, agents and the dashboard all call."""

import argparse
import getpass
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from . import (checks, config, design, detect, events, generate, github, gitops, handover, hosts, human, prbody, release,
               prsync, state, testenv, testreport, trains, trace, usage, validate)
from .stages import BY_NAME, SIZES, STAGE_NAMES, WORK_TYPES

ICONS = {state.PENDING: "⏳", state.IN_PROGRESS: "🔄", state.AWAITING: "🟡", state.APPROVED: "✅",
         state.SKIPPED: "–", state.BLOCKED: "🔴"}
SHORT = {"requirements": "Req", "design": "Design", "planning": "Plan", "development": "Dev", "cicd": "CI/CD", "qa": "QA",
         "release": "Release", "production": "Prod", "monitoring": "Monitor"}


def _out(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _who() -> str:
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
    except OSError:
        name = ""
    return name or getpass.getuser()


def _ctx() -> tuple[Path, dict[str, Any]]:
    root = config.find_root()
    return root, config.load(root)


# ---- setup ----

def cmd_detect(a: argparse.Namespace) -> None:
    from . import standards

    root = Path(a.path).resolve()
    _out({**detect.detect(root), "standards_found": standards.detect(root),
          "standards_default": [{"name": n, "title": s.title, "about": s.about} for n, s in standards.BUILTIN.items()]})


def cmd_init(a: argparse.Namespace) -> None:
    root = Path(a.path).resolve()
    raw = sys.stdin.read() if a.answers == "-" else Path(a.answers).read_text()
    answers = json.loads(raw)
    cfg = config.build_config(answers)
    path = config.write(root, cfg)
    done = []
    if answers.get("templates_from"):  # the team's own templates replace the defaults before anything is written
        from . import standards

        folder = (root / answers["templates_from"]).resolve()
        if not folder.is_dir():
            raise state.StateError(f"templates_from: {folder} is not a folder")
        done += standards.import_templates(root, folder)
    done += generate.scaffold(root, cfg)
    if "copilot" in (cfg.get("integrations") or {}).get("hosts", ["claude"]):
        done += hosts.export_copilot(root, cfg)
    events.emit(root, "project_initialized", detail=cfg["project"]["name"])
    _out({"config": str(path), "generated": done})


# ---- work items ----

def _links_text(links: list[dict[str, str]]) -> str:
    return ", ".join(link["req"] for link in links)


def new_work(root: Path, cfg: dict[str, Any], title: str, wtype: str, links: list[dict[str, str]],
             request: str = "", by: str = "") -> dict[str, Any]:
    """A new work item and its spec. Used by the CLI and the dashboard (`request`: the words it was asked in)."""
    item = state.new_item(root, cfg, title, wtype, links)
    generate.ensure_templates(root, cfg)  # repos set up earlier get new templates and product docs
    wt = config.work_type(cfg, wtype)
    spec = generate.new_spec(root, item["id"], title, wt["template"], _links_text(links))
    if request.strip():  # the agents start from the request as it was written
        text = spec.read_text()
        head, _, rest = text.partition("\n\n")
        quoted = "\n".join(f"> {ln}" for ln in request.strip().splitlines())
        spec.write_text(f"{head}\n\n## Request\n{quoted}\n\n<!-- Asked by {by or 'a person'} in the dashboard. -->\n\n{rest}")
    state.add_artifact(root, item["id"], str(spec.relative_to(root)), item["current_stage"])
    return {"req": item["id"], "title": title, "type": wtype, "links": links, "stage": item["current_stage"],
            "stages": [s for s in STAGE_NAMES if state.runs(item, cfg, s)], "spec": str(spec.relative_to(root))}


def cmd_new(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    links = ([{"rel": "updates", "req": r} for r in a.updates] + [{"rel": "fixes", "req": r} for r in a.fixes]
             + [{"rel": "blocked_by", "req": r} for r in a.blocked_by])
    _out(new_work(root, cfg, a.title, a.type, links))


def cmd_depends(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    on = state.depend(root, a.req, a.on)
    print(f"{a.req} waits for {', '.join(on)} before Development")


def cmd_size(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    state.set_size(root, a.req, a.size)
    item = state.load(root, a.req)
    design = "runs" if state.runs(item, cfg, "design") else "is skipped"
    print(f"{a.req}: size {item['size']}; Design {design} for this {state.item_type(item)}")


def retype(root: Path, cfg: dict[str, Any], req: str, wtype: str, by: str, reason: str) -> tuple[str, str, Path]:
    """Change the type and put the spec on the new type's template. Used by the CLI and the dashboard."""
    if not reason.strip():
        raise state.StateError("Say why the type changes, e.g. sdlc retype REQ-004 change it works as specified")
    old = state.item_type(state.load(root, req))
    first = state.retype(root, req, cfg, wtype, by, reason)
    item = state.load(root, req)
    spec = generate.retemplate_spec(root, req, item["title"], config.work_type(cfg, wtype)["template"],
                                    _links_text(item.get("links", [])), old)
    return old, first, spec


def cmd_retype(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    reason = a.message or " ".join(a.words)
    if not reason.strip():
        raise state.StateError("Say why the type changes, e.g. sdlc retype REQ-004 change it works as specified")
    _human(a, root, cfg, "retype", a.req, {"type": a.type, "reason": reason},
           f"Change {a.req} to a {a.type}: {reason}")


def cmd_next(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    _out(state.next_action(root, cfg, a.req))


def _set_active(root: Path, req: str, stage: str) -> None:
    """Tell the plugin hook which work item the main session is on (fallback tagging)."""
    run = root / ".sdlc" / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "active.json").write_text(json.dumps({"req": req, "stage": stage}))


def cmd_start(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    stage = state.start_stage(root, a.req)
    _set_active(root, a.req, stage)
    if stage == "design":
        generate.ensure_templates(root, cfg)
        pack = design.new_pack(root, a.req, state.load(root, a.req)["title"])
        state.add_artifact(root, a.req, str(pack.relative_to(root)))
    elif stage == "release":
        generate.ensure_templates(root, cfg)
        for p in release.new_pack(root, a.req, state.load(root, a.req)["title"]):
            state.add_artifact(root, a.req, str(p.relative_to(root)))
    elif stage == "qa":
        generate.ensure_templates(root, cfg)
        item = state.load(root, a.req)
        for p in testreport.new_pack(root, a.req, item["title"], state.has_ui(root, item, cfg)):
            state.add_artifact(root, a.req, str(p.relative_to(root)))
    elif stage == "planning":
        generate.ensure_templates(root, cfg)
        for p in generate.new_plan_pack(root, a.req, state.load(root, a.req)["title"]):
            state.add_artifact(root, a.req, str(p.relative_to(root)))
    prsync.sync(root, cfg, a.req)
    print(f"{a.req}: {stage} started")


def cmd_prepare(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    state.load(root, a.req)  # fail early on an unknown REQ
    result = gitops.prepare(root, a.req)
    events.emit(root, "branch_ready", req=a.req, stage="development",
                detail=f"{result['branch']} @ {result['head']}, committed {len(result['committed'])} SDLC files")
    _out(result)


def _pr_enabled(cfg: dict[str, Any]) -> bool:
    return bool((cfg.get("integrations") or {}).get("pull_requests"))


def _base_branch(root: Path, cfg: dict[str, Any]) -> str:
    base = (cfg.get("integrations") or {}).get("base_branch")
    if base:
        return base
    for b in ("main", "master"):
        if gitops.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}", check=False).strip():
            return b
    return "main"


def _require_pr_current(root: Path, cfg: dict[str, Any], req: str) -> None:
    """With pull requests on, Development finishes only when the PR shows the branch as it is now."""
    if not _pr_enabled(cfg):
        return
    pr = state.load(root, req).get("pr")
    head = gitops.git(root, "rev-parse", "--short", f"sdlc/{req}", check=False).strip()
    if not pr:
        raise state.StateError(f"{req}: open the pull request first: `sdlc pr {req}`")
    if pr.get("head") != head:
        raise state.StateError(f"{req}: the branch has commits the PR does not have yet: run `sdlc pr {req}`")


def _lint(root: Path, cfg: dict[str, Any], req: str) -> dict[str, Any]:
    item = state.load(root, req)
    wtype = state.item_type(item)
    return validate.spec_lint(root, req, wtype, config.work_type(cfg, wtype)["template"])


def _require_validations(root: Path, cfg: dict[str, Any], req: str) -> None:
    """Deterministic checks before a stage is handed to a person: spec lint, plan coverage, the release version."""
    item = state.load(root, req)
    stage = item["current_stage"]
    rules = cfg["stages"].get(stage, {})
    if stage == "requirements" and rules.get("require_lint", True):
        r = _lint(root, cfg, req)
        if not r["ok"]:
            raise state.StateError(f"{req}: the spec is not complete (`sdlc lint {req}`): " + "; ".join(r["errors"][:8])
                                   + (f" and {len(r['errors']) - 8} more" if len(r["errors"]) > 8 else ""))
    if stage == "planning" and rules.get("require_plan_check", True):
        r = validate.plan_check(root, req, item["tasks"])
        if not item["tasks"]:
            raise state.StateError(f"{req}: no task plan loaded: `sdlc plan {req}`")
        if not r["ok"]:
            raise state.StateError(f"{req}: the plan does not cover the work (`sdlc plan {req}`): "
                                   + "; ".join([*(f"{x} has no task" for x in r["missing_must"] + r["missing_ids"]),
                                                *r["overlaps"]][:8]))
    if stage == "release":
        want = item.get("version_set") or validate.next_version(root, req, state.item_type(item))["version"]
        have = release.version(root, req)
        if have and have != want:
            raise state.StateError(f"{req}: the release notes say {have} but the version is {want} "
                                   f"(`sdlc version {req} --write`, or `--set` to choose another)")


def cmd_github_protect(a: argparse.Namespace) -> None:
    """A person makes the base branch require the Agentic SDLC statuses, so GitHub blocks merging until QA and
    Release are approved. Changes repository settings, so it runs only for a person in a terminal."""
    root, cfg = _ctx()
    if not human.interactive():
        raise state.StateError("Changing branch protection is done by a person in a terminal; nothing changed")
    base = _base_branch(root, cfg)
    contexts = [f"{github.STATUS_PREFIX}{name}" for name in prsync.GATES.values()]
    summary = (f"Protect `{base}` on GitHub: require {', '.join(contexts)}"
               + (f" and {a.reviews} approving review(s)" if a.reviews else "")
               + ("; admins too" if a.enforce_admins else "; admins can override"))
    if not human.confirm_in_terminal(summary, base, a.yes):
        raise state.StateError("Not confirmed; nothing changed")
    try:
        payload = github.protect(root, base, contexts, a.enforce_admins, a.reviews)
    except github.GitHubError as e:
        if "Upgrade to GitHub Pro" in str(e) or "403" in str(e):
            raise state.StateError(
                "GitHub refused: branch protection on a private repository needs GitHub Pro, Team or Enterprise "
                "(or a public repository). Draft PRs and the Agentic SDLC status checks still work; they just "
                "do not block the Merge button.") from e
        raise
    events.emit(root, "branch_protected", by=_who(), detail=f"{base}: {', '.join(payload['required_status_checks']['contexts'])}")
    _out({"branch": base, **payload})


def cmd_complete(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    _require_validations(root, cfg, a.req)
    if state.load(root, a.req)["current_stage"] == "development":
        _require_pr_current(root, cfg, a.req)
    if state.load(root, a.req)["current_stage"] == "qa":  # the approver sees an up-to-date report
        summary, _ = testreport.write(root, a.req)
        state.add_artifact(root, a.req, str(summary.relative_to(root)))
    result = state.complete_stage(root, a.req, cfg, a.message or "")
    item = state.load(root, a.req)
    if result == state.AWAITING:
        cur = item["current_stage"]
        print(f"{a.req}: {cur} is waiting for approval from {', '.join(item['stages'][cur]['gate'])}")
    else:
        print(f"{a.req}: stage done; now at {item['current_stage'] or 'finished'}")


def approve(root: Path, cfg: dict[str, Any], req: str, by: str, comment: str, via_train: bool = False) -> str | None:
    """Approve the gate. A design approval is also written into the design pack and accepts the item's ADRs."""
    item = state.load(root, req)
    stage = item["current_stage"]
    if stage == "release" and item.get("train") and not via_train:
        raise state.StateError(f"{req} ships in release train {item['train']}: approve the train "
                               f"(`sdlc train approve {item['train']}`), not the item")
    nxt = state.approve(root, req, cfg, by, comment)
    if stage in prsync.GATES:
        prsync.record_approval_head(root, req, stage)
    recorded = (design.record_approval(root, req, by, comment) if stage == "design"
                else testreport.record_signoff(root, req, by, comment) if stage == "qa"
                else release.record_approval(root, req, by, comment) if stage == "release" else [])
    for path in recorded:
        events.emit(root, "artifact", req=req, stage=stage, detail=f"{path} (decision recorded)")
    return nxt


# ---- human-only actions: run only for a person (terminal or dashboard); otherwise filed as pending ----

def _exec_approve(root: Path, cfg: dict[str, Any], req: str, args: dict[str, Any], by: str) -> str:
    stage = state.load(root, req)["current_stage"]
    nxt = approve(root, cfg, req, by, args.get("comment", ""))
    return f"{req}: {stage} approved by {by}. Next stage: {nxt or 'none (work item done)'}"


def _exec_reject(root: Path, cfg: dict[str, Any], req: str, args: dict[str, Any], by: str) -> str:
    state.reject(root, req, by, args["comment"])
    return f"{req}: sent back to the agents with {by}'s feedback"


def _exec_retype(root: Path, cfg: dict[str, Any], req: str, args: dict[str, Any], by: str) -> str:
    old, first, spec = retype(root, cfg, req, args["type"], by, args["reason"])
    return f"{req}: {old} → {args['type']}. Restarts at {first}; the old spec text is kept at the end of {spec.relative_to(root)}"


def _exec_skip(root: Path, cfg: dict[str, Any], req: str, args: dict[str, Any], by: str) -> str:
    nxt = state.skip_stage(root, req, cfg, f"{args['reason']} (by {by})")
    return f"{req}: skipped by {by}. Next stage: {nxt or 'none (work item done)'}"


def _exec_defect(root: Path, cfg: dict[str, Any], req: str, args: dict[str, Any], by: str) -> str:
    d = state.update_defect(root, req, args["defect"], args["decision"], note=args["reason"], by=by)
    return f"{req}/{d['id']}: {d['status']} by {by}"


def _exec_unblock(root: Path, cfg: dict[str, Any], req: str, args: dict[str, Any], by: str) -> str:
    status = state.update_task(root, req, cfg, args["task"], "unblock", f"unblocked by {by}")
    return f"{req}/{args['task']}: {status}"


def _exec_deployed(root: Path, cfg: dict[str, Any], req: str, args: dict[str, Any], by: str) -> str:
    item = state.load(root, req)
    version = args.get("version") or release.version(root, req)
    out = []
    if args.get("tag"):
        if not version:
            raise state.StateError("Give the version: --version v1.2.0 (or set it in the release notes)")
        base = _base_branch(root, cfg)
        remote = (cfg.get("integrations") or {}).get("remote", "origin")
        has_remote = remote in gitops.git(root, "remote").split()
        if has_remote:
            gitops.git(root, "fetch", "-q", remote, base)
        sha = gitops.tag(root, version, f"{remote}/{base}" if has_remote else base, f"{req}: {item['title']}", remote)
        out.append(f"tagged {version} at {sha}")
    release.deployed(root, req, args["env"], by, version, args.get("note", ""))
    out.append(f"{req}: recorded deploy to {args['env']} by {by}" + (f" ({version})" if version else "")
               + f". Next: `sdlc smoke {req} {args['env']}`")
    return "\n".join(out)


def _exec_train_approve(root: Path, cfg: dict[str, Any], name: str, args: dict[str, Any], by: str) -> str:
    done = trains.approve(root, cfg, name, by, args.get("comment", ""),
                          lambda r, c, req, b, cm: approve(r, c, req, b, cm, via_train=True))
    trains.notes(root, name)
    return f"train {name}: Release approved for {len(done)} items by {by}"


def _exec_train_deployed(root: Path, cfg: dict[str, Any], name: str, args: dict[str, Any], by: str) -> str:
    train = trains.load(root, name)
    out = []
    if args.get("tag"):
        base = _base_branch(root, cfg)
        remote = (cfg.get("integrations") or {}).get("remote", "origin")
        has_remote = remote in gitops.git(root, "remote").split()
        if has_remote:
            gitops.git(root, "fetch", "-q", remote, base)
        sha = gitops.tag(root, train["version"], f"{remote}/{base}" if has_remote else base,
                         f"Release {train['version']} ({name}): {', '.join(train['items'])}", remote)
        out.append(f"tagged {train['version']} at {sha}")
    for req in train["items"]:
        release.deployed(root, req, args["env"], by, train["version"], f"train {name}")
    if args["env"] in ("production", "prod", "live"):
        train["status"] = "released"
        (root / ".sdlc" / "trains" / f"{name}.json").write_text(json.dumps(train, indent=2))
    out.append(f"train {name}: recorded deploy of {len(train['items'])} items to {args['env']} by {by}. "
               f"Next: `sdlc smoke <REQ> {args['env']}` for each item")
    return "\n".join(out)


HUMAN_EXEC = {"approve": _exec_approve, "train-approve": _exec_train_approve, "train-deployed": _exec_train_deployed, "reject": _exec_reject, "retype": _exec_retype, "skip": _exec_skip,
              "defect-decision": _exec_defect, "unblock": _exec_unblock, "deployed": _exec_deployed}


def run_human(root: Path, cfg: dict[str, Any], action: str, req: str, args: dict[str, Any], by: str, via: str) -> str:
    """Run a human-only action once a person is known to be present (terminal confirmation or the dashboard)."""
    message = HUMAN_EXEC[action](root, cfg, req, args, by)
    events.emit(root, "human_action", req=req if req.startswith("REQ-") else None, by=by,
                detail=f"{human.ACTIONS[action]} via {via}" + ("" if req.startswith("REQ-") else f" ({req})"))
    affected = [req] if req.startswith("REQ-") else trains.load(root, req)["items"]
    for r in affected:  # each PR's statuses, draft state and label follow the decision
        prsync.sync(root, cfg, r)
    return message


def _human(a: argparse.Namespace, root: Path, cfg: dict[str, Any], action: str, req: str, args: dict[str, Any],
           summary: str) -> None:
    state.load(root, req)  # fail early on an unknown work item
    by = getattr(a, "by", None) or _who()
    if not human.interactive():
        raise human.NeedsHuman(human.request(root, action, req, args, summary))
    if not human.confirm_in_terminal(f"{summary} (as {by})", req, getattr(a, "yes", False)):
        raise state.StateError("Not confirmed; nothing changed")
    print(run_human(root, cfg, action, req, args, by, "terminal"))


def cmd_approve(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    comment = a.message or " ".join(a.words)
    stage = state.load(root, a.req)["current_stage"]
    _human(a, root, cfg, "approve", a.req, {"comment": comment},
           f"Approve the {stage} gate of {a.req}" + (f": {comment}" if comment else ""))


def cmd_reject(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    feedback = a.message or " ".join(a.words)
    if not feedback:
        raise state.StateError("Say what to change, e.g. sdlc reject REQ-001 handle partial refunds")
    _human(a, root, cfg, "reject", a.req, {"comment": feedback}, f"Send {a.req} back: {feedback}")


def cmd_skip(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    stage = state.load(root, a.req)["current_stage"]
    _human(a, root, cfg, "skip", a.req, {"reason": a.message}, f"Skip the {stage} stage of {a.req}: {a.message}")


def cmd_usage(a: argparse.Namespace) -> None:
    """AI usage and estimated cost: total, then per stage, agent and model (and per item without a REQ)."""
    root, cfg = _ctx()
    if a.req:
        state.load(root, a.req)
    s = usage.summarize(events.read(root), cfg, a.req)
    if a.json:
        _out(s)
        return
    t = s["total"]
    print(f"AI usage{' for ' + a.req if a.req else ''}: {usage.human_tokens(t['tokens'])} tokens, "
          f"~${t['cost_usd']:.2f} estimated"
          + (f" (dashboard runs reported ${s['runs_actual_usd']:.2f})" if s["runs_actual_usd"] else ""))
    for dim in (("stage", "agent", "model") if a.req else ("item", "stage", "agent", "model")):
        rows = s[f"by_{dim}"]
        if rows:
            print(f"\nBy {dim}:")
            for r in rows[:15]:
                print(f"  {r['name']:<28} {usage.human_tokens(r['tokens']):>7} tokens  ~${r['cost_usd']:.2f}"
                      + (f"  ({usage.human_tokens(r['unpriced_tokens'])} not priced)" if r["unpriced_tokens"] else ""))
    print(f"\n{s['note']}")


def cmd_pending(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    items = human.pending(root)
    if not items:
        print("No requests waiting for a person.")
    for p in items:
        print(f"{p['id']:<34} {p['ts'][:16].replace('T', ' ')}  {p['summary']}  (from {p['from']})")


def cmd_confirm(a: argparse.Namespace) -> None:
    """A person confirms a pending request in a terminal (or dismisses it with --dismiss)."""
    root, cfg = _ctx()
    if not human.interactive():
        raise state.StateError("Confirming is done by a person in a terminal or the dashboard; nothing changed")
    try:
        p = next(x for x in human.pending(root) if x["id"] == a.id)
    except StopIteration:
        raise state.StateError(f"No pending request {a.id}; see `sdlc pending`") from None
    by = a.by or _who()
    if a.dismiss:
        human.take(root, a.id)
        print(f"Dismissed: {p['summary']}")
        return
    if not human.confirm_in_terminal(f"{p['summary']} (as {by}; requested from {p['from']})", p["req"], a.yes):
        raise state.StateError("Not confirmed; the request is still pending")
    human.take(root, a.id)
    print(run_human(root, cfg, p["action"], p["req"], p["args"], by, "terminal"))


def cmd_plan(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    path = Path(a.file) if a.file else root / ".sdlc" / "plans" / f"{a.req}.json"
    data = json.loads(path.read_text())
    tasks = data["tasks"] if isinstance(data, dict) else data
    state.set_plan(root, a.req, tasks)
    item = state.load(root, a.req)
    w = state.waves(item["tasks"])
    check = validate.plan_check(root, a.req, item["tasks"])
    _out({"req": a.req, "tasks": [t["id"] for t in item["tasks"]], "waves": w, "critical_path_length": len(w),
          "ready_now": [t["id"] for t in state.ready_tasks(item, cfg)], "coverage": check})
    if not check["ok"]:
        raise state.StateError("the plan was loaded but does not cover the work yet; fix it and load it again")


def cmd_task(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    if a.action == "unblock":
        _human(a, root, cfg, "unblock", a.req, {"task": a.task}, f"Unblock {a.req}/{a.task} and let agents retry it")
        return
    status = state.update_task(root, a.req, cfg, a.task, a.action, a.message or "")
    print(f"{a.req}/{a.task}: {status}")


def cmd_design(a: argparse.Namespace) -> None:
    """Create a workstream file of the design pack from its template (agents call this before writing)."""
    root, _ = _ctx()
    item = state.load(root, a.req)
    try:
        p = design.workstream(root, a.req, item["title"], a.workstream)
    except ValueError as e:
        raise state.StateError(str(e)) from e
    state.add_artifact(root, a.req, str(p.relative_to(root)), "design")
    print(p.relative_to(root))


def cmd_adr(a: argparse.Namespace) -> None:
    """Create the next numbered ADR for a decision that outlives the work item."""
    root, _ = _ctx()
    state.load(root, a.req)
    p = design.new_adr(root, a.req, a.title)
    state.add_artifact(root, a.req, str(p.relative_to(root)), "design")
    print(p.relative_to(root))


def cmd_templates(a: argparse.Namespace) -> None:
    """Compare the repo's templates with Agentic SDLC's defaults; --update copies a default over the repo's copy."""
    root, cfg = _ctx()
    if a.update:
        names = list(generate.PROJECT_TEMPLATES) if a.update == ["all"] else a.update
        for name in names:
            if name not in generate.PROJECT_TEMPLATES:
                raise state.StateError(f"Unknown template '{name}'. Known: {', '.join(generate.PROJECT_TEMPLATES)}")
            (root / generate.PROJECT_TEMPLATES[name]).write_text(generate.template(name))
            print(f"updated {generate.PROJECT_TEMPLATES[name]} from the default {name}")
        return
    generate.ensure_templates(root, cfg)
    w = max(map(len, generate.PROJECT_TEMPLATES.values()))
    for name, rel in generate.PROJECT_TEMPLATES.items():
        same = (root / rel).read_text() == generate.template(name)
        print(f"{rel:<{w}}  {'same as default' if same else 'differs from default (yours is used)'}  [{name}]")
    print("\nTo take a newer default: sdlc templates --update <name>  (overwrites your copy; commit it first)")


def cmd_standards(a: argparse.Namespace) -> None:
    """List the standards and skills agents follow; `use` points one at the team's own file, `reset` goes back."""
    from . import standards

    root, cfg = _ctx()
    if a.action == "use":
        if not a.name or not a.path:
            raise state.StateError("Usage: sdlc standards use <name> <path> [--agents a,b]")
        if not (root / a.path).is_file():
            raise state.StateError(f"{a.path} does not exist")
        raw = yaml.safe_load((root / config.CONFIG_FILE).read_text())
        entry: Any = a.path if not a.agents else {"path": a.path, "agents": a.agents.split(",")}
        raw.setdefault("standards", {})[a.name] = entry
        config.validate(raw)
        config.write(root, raw)
        generate.write_claude_md(root, config.load(root))
        print(f"{a.name}: agents now follow {a.path}")
        return
    if a.action == "reset":
        raw = yaml.safe_load((root / config.CONFIG_FILE).read_text())
        (raw.get("standards") or {}).pop(a.name, None)
        if not raw.get("standards"):
            raw.pop("standards", None)
        config.write(root, raw)
        cfg = config.load(root)
        standards.write_defaults(root, cfg)
        generate.write_claude_md(root, cfg)
        print(f"{a.name}: back to {standards.DEFAULT_DIR}/{a.name}.md")
        return
    rows = standards.resolve(root, cfg)
    if a.json:
        _out(rows)
        return
    label = {"default": "Agentic SDLC default", "company": "company default", "edited": "default, edited here",
             "yours": "yours", "missing": "MISSING"}
    w = max(len(r["path"]) for r in rows)
    for r in rows:
        print(f"{r['title']:<32} {r['path']:<{w}}  {label[r['source']]:<22} {len(r['agents'])} agents")
    print("\nUse your own: sdlc standards use coding CONTRIBUTING.md   ·   back to the default: sdlc standards reset coding")


def cmd_trace(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    path, t = trace.write(root, a.req)
    rel = str(path.relative_to(root))
    item = state.load(root, a.req)
    if item["current_stage"]:
        state.add_artifact(root, a.req, rel)
    events.emit(root, "trace_built", req=a.req, detail=f"{len(t['rows']) - len(t['uncovered'])}/{len(t['rows'])} ACs have tests")
    _out({"file": rel, "acceptance_criteria": len(t["rows"]), "without_tests": t["uncovered"],
          "must_without_tests": t["uncovered_must"]})


def cmd_lint(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    r = _lint(root, cfg, a.req)
    _out(r)
    if not r["ok"]:
        raise state.StateError(f"{len(r['errors'])} problem(s) in specs/{a.req}.md")


def cmd_version(a: argparse.Namespace) -> None:
    """The release version from git tags, the work type and breaking changes; --write puts it in the notes."""
    root, _ = _ctx()
    item = state.load(root, a.req)
    if a.set:
        if not validate.SEMVER.match(a.set):
            raise state.StateError(f"'{a.set}' is not a semantic version like v1.2.0")
        with state.edit(root, a.req) as it:
            it["version_set"] = a.set if a.set.startswith("v") else f"v{a.set}"
            it["history"].append({"ts": events.now(), "what": "version_set", "version": it["version_set"],
                                  "by": a.by or _who()})
        item = state.load(root, a.req)
    v = validate.next_version(root, a.req, state.item_type(item))
    if item.get("version_set"):
        v = {**v, "version": item["version_set"], "reason": f"set by hand (computed: {v['version']}, {v['reason']})"}
    if a.write:
        notes = release.pack(root, a.req) / "release-notes.md"
        if not notes.exists():
            raise state.StateError(f"No release notes yet: they are created when the Release stage starts")
        lines = notes.read_text().splitlines()
        lines[0] = f"Version: {v['version']}"
        notes.write_text("\n".join(lines) + "\n")
    _out(v)


def cmd_check(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    state.load(root, a.req)
    report = checks.run(root, a.req, cfg)
    state.record_checks(root, a.req, report)
    state.add_artifact(root, a.req, f"docs/dev/{a.req}/checks.md", "development")
    _out({"passed": report["passed"], "head": report["head"], "report": f"docs/dev/{a.req}/checks.md",
          "results": {r["name"]: r["status"] for r in report["results"]}, "not_configured": report["not_configured"]})
    if not report["passed"]:
        raise state.StateError("checks failed; see the report")


def cmd_fix(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    tid = state.add_task(root, a.req, a.title, a.covers, a.after)
    print(f"{a.req}/{tid}: added ({a.title})")


def defect(root: Path, req: str, did: str, action: str, by: str, note: str = "", **kw: Any) -> dict[str, Any]:
    """Change a defect. Deferring or accepting one is a human decision (the hook blocks models from it)."""
    return state.update_defect(root, req, did, action, note=note, by=by, **kw)


def cmd_defect(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    if a.target == "add":
        if not a.words:
            raise state.StateError('Give the defect a title: sdlc defect REQ-001 add "what is wrong" --severity high')
        did = state.add_defect(root, a.req, " ".join(a.words), a.severity or "medium", bool(a.blocker), a.covers,
                               a.found_by or "", a.message or "")
        print(f"{a.req}/{did}: recorded")
        return
    action = a.words[0] if a.words else "update"
    if action in state.HUMAN_ONLY:
        if not (a.message or "").strip():
            raise state.StateError(f"Say why the defect is {state.D_ACTIONS[action]} (-m)")
        cfg = config.load(root)
        _human(a, root, cfg, "defect-decision", a.req, {"defect": a.target, "decision": action, "reason": a.message},
               f"{action.capitalize()} {a.req}/{a.target}: {a.message}")
        return
    d = defect(root, a.req, a.target, action, a.by or _who(), a.message or "", severity=a.severity,
               blocker=a.blocker, of=a.of)
    print(f"{a.req}/{d['id']}: {d['status']}, {d['severity']}{', blocker' if d['blocker'] else ''}"
          + (f", fix task {d['task']}" if d["task"] else ""))


def cmd_test_report(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    prsync.sync(root, cfg, a.req)  # the QA status shows release blockers as they are triaged
    summary, r = testreport.write(root, a.req)
    state.add_artifact(root, a.req, str(summary.relative_to(root)))
    state.add_artifact(root, a.req, f"docs/qa/{a.req}/defects.md")
    _out({"summary": str(summary.relative_to(root)), "exit_criteria_met": r["met"], "verdicts": r["verdicts"],
          "open_blockers": r["blockers"], "unmet": [n for n, ok in r["criteria"] if not ok]})


def cmd_pr(a: argparse.Namespace) -> None:
    """Commit the evidence, push sdlc/<REQ> and open or update its pull request from the PR template."""
    root, cfg = _ctx()
    item = state.load(root, a.req)
    branch = f"sdlc/{a.req}"
    current = gitops.git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if current != branch:
        raise state.StateError(f"Check out {branch} first (now on {current})")
    generate.ensure_templates(root, cfg)  # the PR template is committed with the evidence
    if item["tasks"]:
        handover.write(root, a.req, cfg)
    gitops.commit_managed(root, a.req, "evidence for review (handover, checks, traceability)")
    head = gitops.push(root, branch, (cfg.get("integrations") or {}).get("remote", "origin"))
    blob = f"https://github.com/{github.repo_slug(root)}/blob/{branch}/"
    draft = (cfg.get("integrations") or {}).get("draft_prs", True)
    pr = github.open_or_update(root, branch, _base_branch(root, cfg), prbody.title(root, a.req),
                               prbody.body(root, a.req, cfg, blob), ["agentic-sdlc", state.item_type(item)], draft=draft)
    state.set_pr(root, a.req, {"number": pr["number"], "url": pr["url"], "head": head,
                               **({"draft": draft} if pr["created"] else {})})
    synced = prsync.sync(root, cfg, a.req) or {}
    _out({"pr": pr["url"], "number": pr["number"], "created": pr["created"], "head": head,
          **{k: synced[k] for k in ("statuses", "draft", "label", "error") if k in synced}})


def _pr_number(root: Path, req: str) -> int:
    pr = state.load(root, req).get("pr")
    if not pr:
        raise state.StateError(f"{req} has no pull request yet: `sdlc pr {req}`")
    return pr["number"]


def cmd_pr_comments(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    _out(github.comments(root, _pr_number(root, a.req)))


def cmd_pr_review(a: argparse.Namespace) -> None:
    """Post dev-pr-reviewer's findings as one COMMENT review with line comments (never an approval)."""
    root, _ = _ctx()
    data = json.loads(Path(a.file).read_text())
    items = data.get("comments", [])
    for c in items:
        if not {"path", "line", "body"} <= c.keys():
            raise state.StateError("Each comment needs path, line and body")
    github.post_review(root, _pr_number(root, a.req), data.get("summary", ""), items)
    events.emit(root, "pr_review", req=a.req, agent="dev-pr-reviewer", detail=f"{len(items)} comments")
    print(f"{a.req}: review posted with {len(items)} comments")


def cmd_pr_reply(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    number = _pr_number(root, a.req)
    if a.resolve:  # agents resolve only the threads an agent opened; people resolve their own
        thread = next((t for t in github.comments(root, number)["threads"] if t["thread"] == a.target), None)
        if not thread or not thread["from_agent"]:
            raise state.StateError("Only threads opened by dev-pr-reviewer can be resolved by an agent; "
                                   "reply and leave a person's thread for them to resolve")
    github.reply(root, number, a.target, a.message, a.resolve)
    events.emit(root, "pr_reply", req=a.req, agent="dev-pr-responder", detail=a.message[:120])
    print(f"{a.req}: replied{' and resolved' if a.resolve else ''}")


def cmd_deploy(a: argparse.Namespace) -> None:
    """Deploy to dev or staging. Production is refused: a person deploys it and runs `sdlc deployed`."""
    root, cfg = _ctx()
    state.load(root, a.req)
    r = release.deploy(root, a.req, a.env, cfg)
    _out({k: r[k] for k in ("env", "commit", "ok", "exit_code", "seconds", "output")})
    if not r["ok"]:
        raise state.StateError(f"deploy to {a.env} failed (exit {r['exit_code']})")


def cmd_deployed(a: argparse.Namespace) -> None:
    """A person records a deploy they did themselves (production). Optionally tags the released commit."""
    root, cfg = _ctx()
    _human(a, root, cfg, "deployed", a.req, {"env": a.env, "version": a.version or "", "tag": a.tag,
                                             "note": a.message or ""},
           f"Record that you deployed {a.req} to {a.env}" + (f" as {a.version}" if a.version else "")
           + (" and tag it" if a.tag else ""))


def cmd_smoke(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    state.load(root, a.req)
    r = release.smoke(root, a.req, a.env, cfg)
    state.add_artifact(root, a.req, r["report"])
    _out({"env": r["env"], "passed": r["passed"], "report": r["report"],
          "results": [{"command": x["command"], "exit_code": x["exit_code"]} for x in r["results"]]})
    if not r["passed"]:
        raise state.StateError(f"smoke check on {a.env} failed; see {r['report']}")


def cmd_handover(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    p = handover.write(root, a.req, cfg)
    state.add_artifact(root, a.req, str(p.relative_to(root)), "development")
    print(p.relative_to(root))


def cmd_artifact(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    state.add_artifact(root, a.req, a.path)
    print(f"{a.req}: recorded {a.path}")


def cmd_event(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    _out(events.emit(root, a.event, req=a.req, stage=a.stage, task=a.task, agent=a.agent,
                     status=a.status, detail=a.detail, source="agent"))


def cmd_log(a: argparse.Namespace) -> None:
    root, _ = _ctx()
    for e in events.read(root, limit=a.n, req=a.req):
        where = "/".join(filter(None, [e.get("req"), e.get("task")])) or "-"
        who = e.get("agent") or e.get("source", "")
        print(f"{e['ts'][11:19]}  {where:<14} {who:<28} {e['event']:<16} {e.get('detail', '')}"[:160])


# ---- status ----

def cmd_status(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    items = [state.load(root, a.req)] if a.req else state.load_all(root)
    if a.json:
        _out(items)
        return
    if not items:
        print("No work items yet. Create one with /sdlc-new \"<title>\".")
        return
    print(f"Agentic SDLC · {cfg['project']['name']}\n")
    print("REQ      Type     " + "".join(f"{SHORT[s]:<9}" for s in STAGE_NAMES) + "Title")
    waiting, blocked = [], []
    for it in items:
        row = f"{it['id']:<9}{state.item_type(it):<9}"
        for s in STAGE_NAMES:
            st = it["stages"][s]
            cell = ICONS.get(st["status"], "?")
            if s == "development" and it["tasks"] and st["status"] == state.IN_PROGRESS:
                done = sum(t["status"] == state.T_DONE for t in it["tasks"])
                cell += f" {done}/{len(it['tasks'])}"
            row += f"{cell:<8}"
            if st["status"] == state.AWAITING:
                waiting.append(f"[{BY_NAME[s].title}] {it['id']} {it['title']} → {', '.join(st['gate'])}")
        blocked += [f"{it['id']}/{t['id']} {t['title']}" for t in it["tasks"] if t["status"] == state.T_BLOCKED]
        links = ", ".join(f"{x['rel']} {x['req']}" for x in it.get("links", []))
        links = f"  ({links})" if links else ""
        print(row + it["title"][:40] + links)
    if a.req and items[0]["tasks"]:
        print("\nTasks:")
        for t in items[0]["tasks"]:
            deps = f" (after {', '.join(t['deps'])})" if t["deps"] else ""
            print(f"  {t['id']:<4} {t['status']:<10} {t['title']}{deps}")
    if waiting:
        print("\n🟡 Waiting for you:\n  " + "\n  ".join(waiting))
    if blocked:
        print("\n🔴 Blocked:\n  " + "\n  ".join(blocked))
    print("\nLegend: ⏳ pending  🔄 running  🟡 waiting for approval  ✅ approved  – skipped  🔴 blocked")


def cmd_train(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    try:
        if a.train_cmd == "new":
            t = trains.create(root, a.name, a.items)
            print(f"train {t['name']}: {', '.join(t['items'])} → {t['version']}")
        elif a.train_cmd in ("add", "remove"):
            t = trains.change(root, a.name, a.items if a.train_cmd == "add" else [], a.items if a.train_cmd == "remove" else [])
            print(f"train {t['name']}: {', '.join(t['items'])} → {t['version']}")
        elif a.train_cmd == "status":
            _out(trains.status(root, a.name))
        elif a.train_cmd == "notes":
            print(trains.notes(root, a.name).relative_to(root))
        elif a.train_cmd in ("approve", "deployed"):
            trains.load(root, a.name)
            action = f"train-{a.train_cmd}"
            args = ({"comment": a.message or ""} if a.train_cmd == "approve"
                    else {"env": a.env, "tag": a.tag})
            summary = (f"Approve release train {a.name}" if a.train_cmd == "approve"
                       else f"Record that you deployed train {a.name} to {a.env}" + (" and tag it" if a.tag else ""))
            by = a.by or _who()
            if not human.interactive():
                raise human.NeedsHuman(human.request(root, action, a.name, args, summary))
            if not human.confirm_in_terminal(f"{summary} (as {by})", a.name, a.yes):
                raise state.StateError("Not confirmed; nothing changed")
            print(run_human(root, cfg, action, a.name, args, by, "terminal"))
    except trains.TrainError as e:
        raise state.StateError(str(e)) from e


def cmd_env(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    try:
        if a.env_cmd == "up":
            _out(testenv.up(root, cfg, a.name, a.port))
        elif a.env_cmd == "down":
            _out(testenv.down(root, cfg, a.name))
        else:
            envs = testenv.listing(root)
            for e in envs:
                print(f"{e['name']:<20} port {e['port']:<6} {e['url']}")
            if not envs:
                print("No test environments are up.")
    except testenv.EnvError as e:
        raise state.StateError(str(e)) from e


def cmd_upgrade(a: argparse.Namespace) -> None:
    from . import upgrade

    root = config.find_root()
    r = upgrade.run(root, a.templates)
    print(f"Upgraded to Agentic SDLC {r['version']}:")
    for line in r["changes"]:
        print(f"  {line}")
    events.emit(root, "upgraded", detail=f"agentic-sdlc {r['version']}: {len(r['changes'])} changes")


def cmd_doctor(a: argparse.Namespace) -> None:
    from . import doctor

    root = config.find_root()
    results = doctor.run(root)
    if a.json:
        _out(results)
    else:
        icon = {"ok": "✓", "warn": "⚠", "fail": "✗"}
        for r in results:
            print(f"{icon[r['status']]} {r['check']:<26} {r['detail']}")
    if doctor.failed(results):
        raise state.StateError("setup problems found (✗ above)")


def cmd_portfolio(a: argparse.Namespace) -> None:
    """All projects at once: items, gates waiting (oldest first), blockers, lead time, rework, AI cost."""
    from . import portfolio

    paths = portfolio.paths_from(a.paths, Path.cwd())
    if a.serve:
        from .dashboard.server import serve_portfolio

        print(f"Agentic SDLC portfolio ({len(paths)} projects): http://{a.host}:{a.port}  (Ctrl+C to stop)")
        serve_portfolio(paths, a.host, a.port)
        return
    data = portfolio.collect(paths)
    if a.json:
        _out(data)
        return
    t = data["totals"]
    print(f"{t['projects']} projects · {t['items_in_flight']} items in flight · {t['gates_waiting']} gates waiting · "
          f"{t['blocked']} blocked · {t['blockers_open']} release blockers · AI ~${t['ai_cost_usd']:.2f} est.\n")
    print(f"{'Project':<24}{'Active':>7}{'Blocked':>8}{'Done':>6}{'Waiting':>8}{'Defects':>8}{'Lead h':>8}{'Rework':>8}"
          f"{'AI $':>8}  Last activity")
    for p in data["projects"]:
        if "error" in p:
            print(f"{p['name']:<24}  error: {p['error'][:80]}")
            continue
        i = p["items"]
        rw = f"{p['rework_rate']:.0%}" if p["rework_rate"] is not None else "-"
        print(f"{p['name'][:23]:<24}{i['active']:>7}{i['blocked']:>8}{i['done']:>6}{len(p['waiting']):>8}"
              f"{p['defects_open']:>8}{p['lead_time_h'] if p['lead_time_h'] is not None else '-':>8}{rw:>8}"
              f"{p['ai']['cost_usd']:>8.2f}  {(p['last_activity'] or '-')[:16].replace('T', ' ')}")
    if data["waiting"]:
        print("\nWaiting for people (oldest first):")
        for w in data["waiting"][:15]:
            print(f"  {w['project']:<20} {w['req']} {w['stage']:<12} {', '.join(w['gate']):<20} "
                  f"{w['hours'] if w['hours'] is not None else '?'} h  {w['title'][:40]}")


def cmd_agents_export(a: argparse.Namespace) -> None:
    """Write the Agentic SDLC agents, orchestrator, prompts, MCP config and AGENTS.md for another AI tool."""
    root, cfg = _ctx()
    if a.host != "copilot":
        raise state.StateError("Claude Code reads the agents from the Agentic SDLC plugin; nothing to export")
    for line in hosts.export_copilot(root, cfg):
        print(line)


def cmd_mcp(a: argparse.Namespace) -> None:
    """Serve the engine over MCP (stdio) for Copilot, Cursor or any MCP host."""
    root = config.find_root(Path(a.root).resolve() if a.root else None)
    from .mcp_server import main as serve_mcp

    serve_mcp(str(root))


def cmd_dashboard(a: argparse.Namespace) -> None:
    root, cfg = _ctx()
    a.port = a.port or int((cfg.get("dashboard") or {}).get("port") or 8765)  # one fixed port per project
    from .dashboard.server import STATIC, serve

    if not (STATIC / "index.html").exists():
        print("sdlc: dashboard UI is not built; API only. Build it with: cd dashboard && npm i && npm run build",
              file=sys.stderr)
    url = f"http://{a.host}:{a.port}"
    running = _dashboard_at(url)
    if running is not None:  # one dashboard per port: reuse it instead of starting a second one elsewhere
        if Path(running).resolve() == root.resolve():
            print(f"Agentic SDLC dashboard for {root.name} is already running: {url}")
            if not a.no_open:
                import webbrowser
                webbrowser.open(url)
            return
        raise state.StateError(f"Port {a.port} already has the dashboard of {running}. Stop that one, or give this "
                               f"project its own port: sdlc dashboard --port <n> (or dashboard.port in sdlc.config.yaml)")
    print(f"Agentic SDLC dashboard for {root.name}: {url}  (Ctrl+C to stop)")
    if not a.no_open:
        import webbrowser
        webbrowser.open(url)
    serve(root, a.host, a.port)


def _dashboard_at(url: str) -> str | None:
    """The project root of an Agentic SDLC dashboard already answering at url, if any."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/api/project", timeout=1) as r:
            return json.loads(r.read()).get("root")
    except (OSError, ValueError):
        return None


def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    p = argparse.ArgumentParser(prog="sdlc", description="Agentic SDLC engine")
    p.add_argument("--version", action="version", version=f"agentic-sdlc {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("detect", help="scan a repo and print the detected stack as JSON")
    s.add_argument("--path", default=".")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("init", help="write sdlc.config.yaml from wizard answers and scaffold the repo")
    s.add_argument("--answers", required=True, help="JSON file with answers, or - for stdin")
    s.add_argument("--path", default=".")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("new", help="create a work item (REQ-xxx) and its spec")
    s.add_argument("title")
    s.add_argument("--type", default="feature", choices=list(WORK_TYPES),
                   help="feature (default) | change: alters existing behaviour | bug | task: no behaviour change")
    s.add_argument("--updates", action="append", default=[], metavar="REQ", help="work item this change modifies")
    s.add_argument("--fixes", action="append", default=[], metavar="REQ", help="work item whose behaviour this bug breaks")
    s.add_argument("--blocked-by", action="append", default=[], metavar="REQ",
                   help="work item that must finish Development before this one starts it")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("depends", help="record that a work item waits for another before Development")
    s.add_argument("req")
    s.add_argument("--on", required=True, metavar="REQ")
    s.set_defaults(fn=cmd_depends)

    s = sub.add_parser("size", help="record the S/M/L size (for a change it decides whether Design runs)")
    s.add_argument("req")
    s.add_argument("size", type=str.upper, choices=list(SIZES))
    s.set_defaults(fn=cmd_size)

    s = sub.add_parser("retype", help="change the work type (human decision); the item restarts at its first stage")
    s.add_argument("req")
    s.add_argument("type", choices=list(WORK_TYPES))
    s.add_argument("words", nargs="*", help="reason (same as -m)")
    s.add_argument("-m", "--message")
    s.add_argument("--by")
    s.set_defaults(fn=cmd_retype)

    for name, fn, hlp in (("next", cmd_next, "what the orchestrator should do now (JSON)"),
                          ("start", cmd_start, "mark the current stage as running")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("req")
        s.set_defaults(fn=fn)

    s = sub.add_parser("prepare", help="switch to branch sdlc/<REQ> and commit SDLC files (spec, ADR, plan)")
    s.add_argument("req")
    s.set_defaults(fn=cmd_prepare)

    s = sub.add_parser("complete", help="agents finished the current stage; wait at its gate")
    s.add_argument("req")
    s.add_argument("-m", "--message", help="short summary for the approver")
    s.set_defaults(fn=cmd_complete)

    s = sub.add_parser("approve", help="approve the gate of the current stage")
    s.add_argument("req")
    s.add_argument("words", nargs="*", help="optional comment (same as -m)")
    s.add_argument("-m", "--message")
    s.add_argument("--by")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="send the current stage back to the agents with feedback")
    s.add_argument("req")
    s.add_argument("words", nargs="*", help="feedback (same as -m)")
    s.add_argument("-m", "--message")
    s.add_argument("--by")
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("skip", help="skip the current stage")
    s.add_argument("req")
    s.add_argument("-m", "--message", required=True)
    s.set_defaults(fn=cmd_skip)

    s = sub.add_parser("plan", help="load the task DAG for a work item")
    s.add_argument("req")
    s.add_argument("--file", help="default: .sdlc/plans/<REQ>.json")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("task", help="update a task: start | review | done | fail | unblock")
    s.add_argument("req")
    s.add_argument("task")
    s.add_argument("action", choices=["start", "review", "done", "fail", "unblock"])
    s.add_argument("-m", "--message")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("design", help="create a design-pack workstream file: " + ", ".join(design.WORKSTREAMS))
    s.add_argument("req")
    s.add_argument("workstream", choices=list(design.WORKSTREAMS))
    s.set_defaults(fn=cmd_design)

    s = sub.add_parser("adr", help="create the next numbered ADR (docs/adr/NNNN-title.md) for a lasting decision")
    s.add_argument("req")
    s.add_argument("title")
    s.set_defaults(fn=cmd_adr)

    s = sub.add_parser("templates", help="show which repo templates differ from the defaults; --update to take a default")
    s.add_argument("--update", nargs="+", metavar="NAME", help="template names, e.g. spec.md, or all")
    s.set_defaults(fn=cmd_templates)

    s = sub.add_parser("standards", help="the standards and skills agents follow (coding, testing, security, ...)")
    s.add_argument("action", nargs="?", choices=["list", "use", "reset"], default="list")
    s.add_argument("name", nargs="?")
    s.add_argument("path", nargs="?")
    s.add_argument("--agents", help="comma-separated agents that follow it (default: the standard's usual agents)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_standards)

    s = sub.add_parser("trace", help="write docs/rtm/<REQ>.md: acceptance criteria → tasks → tests, and the decision log")
    s.add_argument("req")
    s.set_defaults(fn=cmd_trace)

    s = sub.add_parser("lint", help="check the spec is complete: every section written, ACs with Given/When/Then and priority")
    s.add_argument("req")
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("version", help="the release version from git tags, work type and breaking changes")
    s.add_argument("req")
    s.add_argument("--write", action="store_true", help="put it on the first line of the release notes")
    s.add_argument("--set", metavar="VERSION", help="choose a different version (recorded in the history)")
    s.add_argument("--by")
    s.set_defaults(fn=cmd_version)

    s = sub.add_parser("check", help="run lint, typecheck, tests, build, audit, SAST and secret scans; write docs/dev/<REQ>/checks.md")
    s.add_argument("req")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("fix", help="add a fix task during Development (integration or verification failure)")
    s.add_argument("req")
    s.add_argument("title")
    s.add_argument("--covers", nargs="*", default=[], metavar="ID", help="ACs or ids the fix restores, e.g. AC-3")
    s.add_argument("--after", nargs="*", default=[], metavar="TASK", help="tasks it must wait for")
    s.set_defaults(fn=cmd_fix)

    s = sub.add_parser("defect", help="QA defects: `defect REQ add <title>` or `defect REQ D1 fix|verified|reopen|duplicate|defer|accept`")
    s.add_argument("req")
    s.add_argument("target", help="add, or a defect id like D1")
    s.add_argument("words", nargs="*", help="title (add) or action (update, fix, verified, reopen, duplicate, defer, accept)")
    s.add_argument("--severity", choices=list(state.SEVERITIES))
    s.add_argument("--blocker", action=argparse.BooleanOptionalAction, default=None, help="release blocker")
    s.add_argument("--covers", nargs="*", default=[], metavar="ID", help="ACs or ids it breaks")
    s.add_argument("--found-by", help="agent or person who found it")
    s.add_argument("--of", help="for duplicate: the defect it duplicates")
    s.add_argument("-m", "--message")
    s.add_argument("--by")
    s.set_defaults(fn=cmd_defect)

    s = sub.add_parser("test-report", help="write docs/qa/<REQ>/summary.md and defects.md: verdicts, defects, exit criteria")
    s.add_argument("req")
    s.set_defaults(fn=cmd_test_report)

    s = sub.add_parser("pr", help="commit the evidence, push sdlc/<REQ> and open or update its pull request")
    s.add_argument("req")
    s.set_defaults(fn=cmd_pr)

    s = sub.add_parser("github", help="GitHub repository settings (person only): `sdlc github protect`")
    gsub = s.add_subparsers(dest="github_cmd", required=True)
    g = gsub.add_parser("protect", help="require the Agentic SDLC QA and Release statuses on the base branch")
    g.add_argument("--enforce-admins", action=argparse.BooleanOptionalAction, default=True,
                   help="also block admins (default); --no-enforce-admins lets admins override in an emergency")
    g.add_argument("--reviews", type=int, default=0,
                   help="approving reviews to require (0: none; a PR's author cannot approve their own PR)")
    g.add_argument("--yes", action="store_true")
    g.set_defaults(fn=cmd_github_protect)

    s = sub.add_parser("pr-comments", help="unresolved review threads and comments on the work item's PR (JSON)")
    s.add_argument("req")
    s.set_defaults(fn=cmd_pr_comments)

    s = sub.add_parser("pr-review", help="post a COMMENT review with line comments from a JSON file")
    s.add_argument("req")
    s.add_argument("--file", required=True, help='{"summary": "...", "comments": [{"path", "line", "body"}]}')
    s.set_defaults(fn=cmd_pr_review)

    s = sub.add_parser("pr-reply", help="reply to a review thread (PRRT_...) or a comment id on the PR")
    s.add_argument("req")
    s.add_argument("target")
    s.add_argument("-m", "--message", required=True)
    s.add_argument("--resolve", action="store_true", help="also resolve the thread (only threads an agent opened)")
    s.set_defaults(fn=cmd_pr_reply)

    s = sub.add_parser("deploy", help="deploy to dev or staging (production is always deployed by a person)")
    s.add_argument("req")
    s.add_argument("env")
    s.set_defaults(fn=cmd_deploy)

    s = sub.add_parser("deployed", help="record a deploy a person did (production); --tag tags the released commit")
    s.add_argument("req")
    s.add_argument("env")
    s.add_argument("--version", help="e.g. v1.2.0 (default: the first line of the release notes)")
    s.add_argument("--tag", action="store_true", help="tag the base branch's head with the version and push the tag")
    s.add_argument("-m", "--message")
    s.add_argument("--by")
    s.set_defaults(fn=cmd_deployed)

    s = sub.add_parser("smoke", help="run an environment's smoke checks; writes docs/release/<REQ>/smoke-<env>.md")
    s.add_argument("req")
    s.add_argument("env")
    s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("handover", help="write docs/dev/<REQ>/handover.md: build, changes, checks, coverage for QA")
    s.add_argument("req")
    s.set_defaults(fn=cmd_handover)

    s = sub.add_parser("artifact", help="record a file produced for the current stage")
    s.add_argument("req")
    s.add_argument("path")
    s.set_defaults(fn=cmd_artifact)

    s = sub.add_parser("event", help="emit a custom event to the activity log")
    s.add_argument("event")
    for f in ("req", "stage", "task", "agent", "status", "detail"):
        s.add_argument(f"--{f}")
    s.set_defaults(fn=cmd_event)

    s = sub.add_parser("log", help="show recent activity")
    s.add_argument("req", nargs="?")
    s.add_argument("-n", type=int, default=30)
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("train", help="release trains: several items, one version, one go/no-go")
    tsub = s.add_subparsers(dest="train_cmd", required=True)
    for name, hlp in (("new", "create a train from work items"), ("add", "add items"), ("remove", "remove items")):
        g = tsub.add_parser(name, help=hlp)
        g.add_argument("name")
        g.add_argument("items", nargs="+", metavar="REQ")
        g.set_defaults(fn=cmd_train)
    for name, hlp in (("status", "each item's stage and release evidence, the train version (JSON)"),
                      ("notes", "write docs/release/trains/<name>.md from the items' release notes")):
        g = tsub.add_parser(name, help=hlp)
        g.add_argument("name")
        g.set_defaults(fn=cmd_train)
    g = tsub.add_parser("approve", help="a person approves every item's Release gate at once")
    g.add_argument("name")
    g.add_argument("-m", "--message")
    g.add_argument("--by")
    g.add_argument("--yes", action="store_true")
    g.set_defaults(fn=cmd_train)
    g = tsub.add_parser("deployed", help="a person records the train's deploy (and tags it once)")
    g.add_argument("name")
    g.add_argument("env")
    g.add_argument("--tag", action="store_true")
    g.add_argument("--by")
    g.add_argument("--yes", action="store_true")
    g.set_defaults(fn=cmd_train)

    s = sub.add_parser("env", help="isolated test environments: `env up <name> --port N`, `env down <name>`, `env list`")
    esub = s.add_subparsers(dest="env_cmd", required=True)
    g = esub.add_parser("up", help="start a copy of the app with its own data and wait until it answers")
    g.add_argument("name")
    g.add_argument("--port", type=int, required=True)
    g.set_defaults(fn=cmd_env)
    g = esub.add_parser("down", help="stop it and delete its data")
    g.add_argument("name")
    g.set_defaults(fn=cmd_env)
    g = esub.add_parser("list")
    g.set_defaults(fn=cmd_env)

    s = sub.add_parser("upgrade", help="bring this repo's config, generated files and templates up to this version")
    s.add_argument("--templates", action="store_true", help="also replace templates that differ from the defaults")
    s.set_defaults(fn=cmd_upgrade)

    s = sub.add_parser("doctor", help="check this repo's setup: versions, company policy, git, GitHub, tools, exports")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("portfolio", help="all projects at once (paths, or `projects:` in org.yaml); --serve for the web view")
    s.add_argument("paths", nargs="*")
    s.add_argument("--json", action="store_true")
    s.add_argument("--serve", action="store_true")
    s.add_argument("--port", type=int, default=8770)
    s.add_argument("--host", default="127.0.0.1")
    s.set_defaults(fn=cmd_portfolio)

    s = sub.add_parser("agents", help="the Agentic SDLC agents for other AI tools: `sdlc agents export --host copilot`")
    asub = s.add_subparsers(dest="agents_cmd", required=True)
    g = asub.add_parser("export", help="write .github/agents, .github/prompts, .vscode/mcp.json and AGENTS.md")
    g.add_argument("--host", choices=list(hosts.HOSTS), default="copilot")
    g.set_defaults(fn=cmd_agents_export)

    s = sub.add_parser("mcp", help="serve Agentic SDLC as an MCP server (stdio) for Copilot, Cursor or any MCP host")
    s.add_argument("--root", help="the repo (default: the folder holding sdlc.config.yaml above the cwd)")
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("dashboard", help="open the live web dashboard for this repo")
    s.add_argument("--port", type=int, help="default: dashboard.port in sdlc.config.yaml, else 8765")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--no-open", action="store_true", help="don't open a browser")
    s.set_defaults(fn=cmd_dashboard)

    s = sub.add_parser("usage", help="AI tokens and estimated cost per item, stage, agent and model")
    s.add_argument("req", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_usage)

    s = sub.add_parser("pending", help="human-only requests waiting for a person to confirm")
    s.set_defaults(fn=cmd_pending)

    s = sub.add_parser("confirm", help="confirm (or --dismiss) a pending human-only request, in a terminal")
    s.add_argument("id")
    s.add_argument("--dismiss", action="store_true")
    s.add_argument("--yes", action="store_true", help="skip typing the work item id")
    s.add_argument("--by")
    s.set_defaults(fn=cmd_confirm)

    for name in ("approve", "reject", "skip", "retype", "deployed", "task", "defect"):  # human-only actions
        sp = sub.choices[name]
        sp.add_argument("--yes", action="store_true", help="in a terminal: skip typing the work item id")
        if not any("--by" in act.option_strings for act in sp._actions):
            sp.add_argument("--by")

    s = sub.add_parser("status", help="pipeline board in the terminal")
    s.add_argument("req", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except human.NeedsHuman as e:
        print(f"sdlc: {e}", file=sys.stderr)
        return 3
    except (config.ConfigError, state.StateError, gitops.GitError, github.GitHubError, release.ReleaseError,
            FileNotFoundError, json.JSONDecodeError) as e:
        print(f"sdlc: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
