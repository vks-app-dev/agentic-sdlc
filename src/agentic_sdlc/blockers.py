"""What a person must sort out before agents can start the current stage, found by rules (no AI, no cost).

`find()` returns named problems with what to do, so the dashboard can list them under "Waiting for you", show a
checklist with buttons, and hold back "Run agents" until they are fixed:

- `uncommitted`: files outside SDLC's own that would stop `sdlc prepare` making the work-item branch;
- `other_branch`: the folder is on another work item's branch, so this item would start from that item's code;
- `spec`: a spec a person writes (a task's, a bug's report) still has empty required sections.
"""

import re
from pathlib import Path
from typing import Any

from . import config, gitops, state

# Item artifacts stay with the item's branch; everything else dirty is project setup (config, standards, scripts).
ITEM_PATHS = (r"specs/REQ-\d+", r"docs/(design|plan|dev|qa|release|rtm)/REQ-\d+", r"docs/adr/\d", r"\.sdlc/")

# Sections a person fills in, per work type, while no agent stage writes the spec for them.
SPEC_SECTIONS: dict[str, list[dict[str, Any]]] = {
    "task": [
        {"name": "What and why", "required": True, "kind": "text",
         "hint": "The technical work and the reason for it, e.g. Upgrade FastAPI 0.110 → 0.115 for a security fix."},
        {"name": "Done when", "required": True, "kind": "checklist",
         "hint": "Checkable outcomes, one per line. A task changes no user-visible behaviour."},
        {"name": "Risk and rollback", "required": False, "kind": "text",
         "hint": "Risk: low / medium / high, because …  Rollback: …"},
        {"name": "Notes", "required": False, "kind": "text", "hint": "Commands, links, files involved."},
    ],
    "bug": [
        {"name": "Report", "required": True, "kind": "text",
         "hint": "What was seen, where and when. Paste error text (remove personal data and secrets)."},
        {"name": "Steps to reproduce", "required": False, "kind": "text", "hint": "1. …"},
        {"name": "Expected vs actual", "required": False, "kind": "text", "hint": "Expected: …  Actual: …"},
    ],
}
# The stage in which each type's person-written spec is needed.
SPEC_STAGE = {"task": "development", "bug": "requirements"}

_HEADING = re.compile(r"^## (.+?)\s*$", re.M)


def is_item_file(path: str) -> bool:
    return any(re.match(p, path) for p in ITEM_PATHS)


def setup_files(root: Path) -> list[str]:
    """Uncommitted files that belong to the project setup, not to one work item."""
    return [p for p in gitops.dirty_paths(root) if not is_item_file(p)]


def sections(text: str) -> dict[str, str]:
    """`## Heading` → its body, without HTML comments."""
    out = {}
    marks = list(_HEADING.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = re.sub(r"<!--.*?-->", "", text[m.end():end], flags=re.S).strip()
    return out


def _filled(kind: str, body: str) -> bool:
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if kind == "checklist":  # at least one real outcome beyond the template's own "Existing tests pass"
        items = [re.sub(r"^[-*]\s*(\[.?\]\s*)?", "", ln).strip() for ln in lines]
        return any(x and x.lower() != "existing tests pass" for x in items)
    texts = [re.sub(r"^[-*\d.]+\s*", "", ln) for ln in lines]
    # a template prompt like "Rollback:" or "Risk: low / medium / high, because" is not an answer
    return any(t and not re.fullmatch(r"[^:]+:\s*(low / medium / high, because)?", t) for t in texts)


def spec_gaps(root: Path, item: dict[str, Any]) -> list[str]:
    wtype = state.item_type(item)
    wanted = SPEC_SECTIONS.get(wtype)
    p = root / "specs" / f"{item['id']}.md"
    if not wanted or not p.exists():
        return []
    have = sections(p.read_text())
    return [s["name"] for s in wanted if s["required"] and not _filled(s["kind"], have.get(s["name"], ""))]


def task_worktrees(root: Path, req: str) -> list[dict[str, Any]]:
    """Working copies (git worktrees) other than this folder that have one of the item's task branches out."""
    out, cur = [], {}
    for line in gitops.git(root, "worktree", "list", "--porcelain", check=False).splitlines() + [""]:
        if not line.strip():
            if cur.get("branch", "").startswith(f"refs/heads/sdlc/{req}-") and Path(cur["path"]) != root.resolve():
                out.append({"path": cur["path"], "branch": cur["branch"].removeprefix("refs/heads/"),
                            "locked": "locked" in cur})
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[9:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:]
        elif line.startswith("locked"):
            cur["locked"] = True
    return out


def free_worktrees(root: Path, req: str) -> dict[str, Any]:
    """A person frees the task branches agents left checked out (the branches and their commits are kept)."""
    freed, kept = [], []
    for w in task_worktrees(root, req):
        changes = gitops.git(Path(w["path"]), "status", "--porcelain", check=False).strip() if Path(w["path"]).exists() else ""
        if changes:
            kept.append(f"{w['branch']} ({w['path']} has uncommitted changes)")
            continue
        gitops.git(root, "worktree", "unlock", w["path"], check=False)
        gitops.git(root, "worktree", "remove", "--force", "--force", w["path"], check=False)
        freed.append(w["branch"])
    gitops.git(root, "worktree", "prune", check=False)
    if kept:
        raise state.StateError("Not freed, it has work that is not committed: " + "; ".join(kept))
    return {"freed": freed}


def _agent_help(root: Path, req: str) -> str | None:
    """What the agents of the latest run asked a person to do (sdlc event human_needed), if anything."""
    from . import events

    asked, started = None, ""
    for e in events.read(root, req=req):
        if e["event"] in ("run_started", "help_handled"):
            started, asked = e["ts"], None
        elif e["event"] == "human_needed" and e["ts"] >= started:
            asked = e.get("detail") or "The agents need a person."
    return asked


# What a person does next when the agents ask for help, by what they said (first match wins). The last entry is
# the fallback for anything not recognised, so there is always a next step.
HELP_RULES: tuple[tuple[str, list[str]], ...] = (
    (r"denied|not allowed|permission", [
        "The agents tried a command they are not allowed to run in an unattended run.",
        "Agentic SDLC now tells agents to read files with their own tools and to run one allowed command at a time, "
        "so this usually does not happen again: press ▶ Run agents again.",
        "If it is refused again, the exact command is shown here: tell whoever maintains Agentic SDLC, or do that "
        "step yourself in a terminal, then run the agents again."]),
    (r"worktree|checked out|locked", [
        "An agent's working copy was left behind: use “Free it” in the list above if it is shown.",
        "Then press ▶ Run agents again."]),
    (r"uncommitted|commit or stash", [
        "Commit the files listed above (“Review & commit”), then run the agents again."]),
    (r"spec|acceptance criteria|done when|template", [
        "Fill in the spec (“Fill in the spec” above; “✨ Suggest with AI” drafts it), then run the agents again."]),
    (r"api key|not logged in|log in|credit|rate limit", [
        "Claude Code could not work: check the login or the account, then run the agents again."]),
    (r"commit.message|reword|rebase", [
        "A commit on the work-item branch does not start with the work item's ID: use “Fix commits” above if it is "
        "shown, then run the agents again."]),
    (r"test(s)? fail|failing test|checks? fail", [
        "Open the run log to see which check failed, and decide: fix it yourself, or run the agents again to let "
        "them retry (they stop after the configured number of attempts)."]),
    (r"decide|decision|which option|clarif|confirm|approve", [
        "Read the files under “Read first” (the report the agents are asking about is first).",
        "Answer in “Your decision”: ✓ Accept & run agents again, or ✗ Not accepted with what they should do instead.",
        "Your answer is written into the spec's Decisions section, so the agents follow it and do not ask again."]),
    (r".*", [
        "Read what the agents said above: it usually names something only a person can do (a decision, an access, "
        "a file, a service).",
        "Do that, then press “Mark as handled” and ▶ Run agents again.",
        "Not sure what it means? Use “✨ Explain with AI” for plain-language steps, or open the run log."]),
)


def help_steps(said: str) -> dict[str, Any]:
    low = said.lower()
    for i, (pattern, steps) in enumerate(HELP_RULES):
        if re.search(pattern, low):
            return {"steps": steps, "known": i < len(HELP_RULES) - 1}
    return {"steps": [], "known": False}


def explain_help(root: Path, req: str, timeout: int = 120) -> dict[str, Any]:
    """Plain-language next steps for a person from what the agents said and the end of the run log (one quick,
    read-only Claude Code call)."""
    import json
    import os
    import subprocess

    said = _agent_help(root, req) or ""
    logs = sorted((root / ".sdlc" / "run" / "logs").glob(f"{req}-*.log"))
    tail = ""
    if logs:
        from .dashboard.runner import readable

        lines = [readable(ln) for ln in logs[-1].read_text(errors="replace").splitlines()]
        tail = "\n".join(x for x in lines if x)[-4000:]
    prompt = ("AI agents working on a software work item stopped and asked a person for help. Turn this into 2 to 4 "
              "short, concrete steps the person can follow now, in plain language, starting with a verb. Mention exact "
              "commands or files only when needed. Reply with ONLY a JSON object: {\"steps\": [\"...\"]}.\n\n"
              f"Work item {req}. What the agents said:\n{said}\n\nEnd of the run log:\n{tail}")
    env = {**os.environ}
    if os.environ.get("SDLC_RUN_AUTH") == "login":
        env.pop("ANTHROPIC_API_KEY", None)
    try:
        r = subprocess.run([os.environ.get("SDLC_CLAUDE_BIN", "claude"), "-p", prompt, "--output-format", "json",
                            "--model", os.environ.get("SDLC_EXPLAIN_MODEL") or "haiku", "--allowedTools", "Read"],
                           cwd=root, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        out = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        raise state.StateError(f"Could not get an explanation from Claude Code: {e}") from e
    if out.get("is_error"):
        raise state.StateError(f"Claude Code: {out.get('result')}")
    m = re.search(r"\{.*\}", str(out.get("result") or ""), re.S)
    try:
        steps = [str(s) for s in json.loads(m.group(0)).get("steps", [])] if m else []
    except json.JSONDecodeError:
        steps = []
    if not steps:
        raise state.StateError("The explanation could not be read; try again or open the run log")
    return {"steps": steps, "cost_usd": out.get("total_cost_usd")}


def find(root: Path, cfg: dict[str, Any], req: str, running: bool = False) -> list[dict[str, Any]]:
    item = state.load(root, req)
    stage = item["current_stage"]
    nxt = state.next_action(root, cfg, req)
    if not stage or nxt["action"] not in ("run_stage", "complete_stage"):
        return []
    out: list[dict[str, Any]] = []
    wtype = state.item_type(item)
    if SPEC_STAGE.get(wtype) == stage and (gaps := spec_gaps(root, item)):
        out.append({"code": "spec", "title": "Fill in the spec",
                    "detail": f"specs/{req}.md still needs: {', '.join(gaps)}. The coder and reviewers work from it.",
                    "sections": gaps, "file": f"specs/{req}.md", "action": "edit_spec"})
    if stage == "development" and (root / ".git").exists():
        branch = gitops.git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
        own = f"sdlc/{req}"
        base = (cfg.get("integrations") or {}).get("base_branch") or "main"
        own_exists = bool(gitops.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{own}", check=False).strip())
        files = setup_files(root)
        foreign = [p for p in files if not gitops.managed(p)]
        if foreign or (files and branch != base):
            out.append({"code": "uncommitted", "title": f"Commit {len(files)} setup file{'s' * (len(files) != 1)}",
                        "detail": "These changes are not committed, so the agents cannot start the work-item branch: "
                                  + ", ".join(files[:6]) + (f" and {len(files) - 6} more" if len(files) > 6 else ""),
                        "files": files, "target": base, "branch": branch, "action": "commit"})
        orphans = [t["id"] for t in item["tasks"] if t["status"] in (state.T_RUNNING, state.T_REVIEW)]
        if not running and orphans:
            out.append({"code": "orphan_task", "title": f"Reset {', '.join(orphans)}: marked as being worked on",
                        "detail": f"{', '.join(orphans)} {'is' if len(orphans) == 1 else 'are'} still marked as in progress "
                                  "from a run that has ended, so no coder will pick it up. Resetting makes it ready to "
                                  "try again (its attempts so far still count).",
                        "tasks": orphans, "action": "reset_tasks"})
        if not running and branch == own and (bad := bad_commits(root, req)):
            out.append({"code": "bad_commits", "title": f"Fix {len(bad)} commit{'s' * (len(bad) != 1)} on {own}",
                        "detail": "Every commit on the branch must start with " + req + ": "
                                  + "; ".join(f"{b['sha']} “{b['subject']}” ({'drop it: ' + b['why'] if b['fix'] == 'drop' else 'add the ID'})"
                                              for b in bad),
                        "commits": bad, "action": "fix_commits"})
        if not running and (stuck := task_worktrees(root, req)):
            names = ", ".join(w["branch"].removeprefix(f"sdlc/{req}-") for w in stuck)
            out.append({"code": "stuck_worktree", "title": f"Free {names}'s working copy",
                        "detail": f"An earlier agent left {', '.join(w['branch'] for w in stuck)} checked out in its own "
                                  "working copy, so a new coder cannot start it. Freeing it keeps the branch and its commits.",
                        "worktrees": stuck, "action": "free_worktrees"})
        m = re.fullmatch(r"sdlc/(REQ-\d+)(-T\d+)?", branch)
        if not own_exists and m and m.group(1) != req:
            out.append({"code": "other_branch", "title": f"Start from {base}, not {m.group(1)}'s branch",
                        "detail": f"This folder is on {branch}, so {req} would start from {m.group(1)}'s unmerged "
                                  f"code. Switch to {base} first (committing the setup files does this).",
                        "branch": branch, "target": base, "action": "switch"})
    if not running and (asked := _agent_help(root, req)):
        # what the agents said; it does not hold the next run back (the engine cannot tell when it is resolved)
        mentioned = [p for p in dict.fromkeys(re.findall(r"[\w./-]+\.(?:md|json|ya?ml|txt)", asked)) if (root / p).is_file()]
        stage_files = [p for p in item["stages"].get(stage, {}).get("artifacts", []) if (root / p).is_file()]
        out.append({"code": "agent_help", "title": "The last run asked for help", "detail": asked,
                    "action": "read", "blocking": False, **help_steps(asked),
                    "files": list(dict.fromkeys(mentioned + stage_files))[:10],
                    "decision": bool(re.search(r"decide|decision|accept|whether|approve|confirm|sign.?off", asked, re.I))})
    return out


def blocking(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in found if b.get("blocking", True)]


def all_items(root: Path, cfg: dict[str, Any], running: set[str] = frozenset()) -> list[dict[str, Any]]:
    out = []
    for it in state.load_all(root):
        if it["status"] == "done":
            continue
        try:
            found = find(root, cfg, it["id"], it["id"] in running)
        except (state.StateError, gitops.GitError):
            continue
        if found:
            out.append({"req": it["id"], "title": it["title"], "type": state.item_type(it), "blockers": found})
    return out


def write_spec(root: Path, item: dict[str, Any], values: dict[str, str]) -> Path:
    """Replace the bodies of the given sections in the item's spec (keeping each section's hint comment)."""
    wanted = {s["name"]: s for s in SPEC_SECTIONS.get(state.item_type(item), [])}
    p = root / "specs" / f"{item['id']}.md"
    text = p.read_text()
    for name, value in values.items():
        if name not in wanted:
            raise state.StateError(f"{name} is not a section you fill in for a {state.item_type(item)}")
        body = value.strip()
        if wanted[name]["kind"] == "checklist":
            body = "\n".join(ln if ln.lstrip().startswith(("- [", "* [")) else f"- [ ] {ln.lstrip('-* ').strip()}"
                             for ln in body.splitlines() if ln.strip())
        m = re.search(rf"^## {re.escape(name)}\s*$", text, re.M)
        if not m:
            text = text.rstrip() + f"\n\n## {name}\n{body}\n"
            continue
        nxt = _HEADING.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        old = text[m.end():end]
        hint = "".join(re.findall(r"\n?<!--.*?-->", old, flags=re.S))
        text = text[:m.end()] + f"{hint}\n{body}\n\n" + text[end:].lstrip("\n")
    p.write_text(text.rstrip() + "\n")
    return p


def spec_form(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    """The sections a person fills in, with what is there now (or a suggestion from the request)."""
    p = root / "specs" / f"{item['id']}.md"
    have = sections(p.read_text()) if p.exists() else {}
    request = "\n".join(ln[1:].strip() for ln in have.get("Request", "").splitlines() if ln.startswith(">")).strip()
    rows = []
    for s in SPEC_SECTIONS.get(state.item_type(item), []):
        body = have.get(s["name"], "")
        filled = _filled(s["kind"], body)
        value = body if filled else ""
        if not filled and s["name"] in ("What and why", "Report") and request:
            value = request
        if s["kind"] == "checklist":
            value = "\n".join(re.sub(r"^[-*]\s*\[.?\]\s*", "", ln).strip() for ln in (value or body).splitlines()
                              if re.sub(r"^[-*]\s*\[.?\]\s*", "", ln).strip())
        rows.append({**s, "value": value, "filled": filled})
    return {"file": f"specs/{item['id']}.md", "sections": rows,
            "gaps": spec_gaps(root, item), "type": state.item_type(item)}


def commit_setup(root: Path, cfg: dict[str, Any], files: list[str], message: str, target: str) -> dict[str, Any]:
    """A person commits project-setup files. To the base branch (default) the commit is made in a temporary
    worktree, then this folder switches to that branch with the files unchanged; to the current branch it is a
    plain commit. Work-item files (specs, plans, state) are never part of it."""
    import shutil
    import tempfile

    dirty = set(setup_files(root))
    files = [f for f in dict.fromkeys(files) if f]
    if not files:
        raise state.StateError("Choose the files to commit")
    if bad := [f for f in files if f not in dirty]:
        raise state.StateError(f"Not uncommitted setup files: {', '.join(bad)}")
    if not message.strip():
        raise state.StateError("Write a commit message")
    branch = gitops.git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if target == branch:
        gitops.git(root, "add", "-A", "--", *files)
        gitops.git(root, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message, "--", *files)
        return {"branch": branch, "commit": gitops.git(root, "rev-parse", "--short", "HEAD").strip(),
                "switched": False, "files": files}
    if not gitops.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{target}", check=False).strip():
        raise state.StateError(f"There is no branch {target}")
    tmp = Path(tempfile.mkdtemp(prefix="sdlc-commit-"))
    wt = tmp / "wt"
    gitops.git(root, "worktree", "add", "--quiet", str(wt), target)
    try:
        for f in files:
            src, dst = root / f, wt / f
            if src.is_dir():
                raise state.StateError(f"{f} is a folder, not a file: leave it out of the commit")
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()
        gitops.git(wt, "add", "-A", "--", *files)
        gitops.git(wt, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message)
        commit = gitops.git(wt, "rev-parse", "--short", "HEAD").strip()
    finally:
        gitops.git(root, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
    # Switch this folder to the target. The committed files equal the target's versions now, so set them aside,
    # switch, and drop them; anything else uncommitted (the item's spec) comes along.
    gitops.git(root, "stash", "push", "--include-untracked", "--quiet", "-m", "sdlc: setup files committed to "
               + target, "--", *files)
    try:
        gitops.git(root, "checkout", "--quiet", target)
    except gitops.GitError:
        gitops.git(root, "stash", "pop", "--quiet", check=False)
        raise
    differ = sorted(set(gitops.dirty_paths(root)) & set(files))  # should be none: the target has these versions
    if differ:
        gitops.git(root, "stash", "pop", "--quiet", check=False)
    else:
        gitops.git(root, "stash", "drop", "--quiet", check=False)
    return {"branch": target, "commit": commit, "switched": True, "from": branch, "files": files,
            "note": f"{branch} is unchanged; this folder is now on {target}." + (f" Check: {differ}" if differ else "")}


def switch(root: Path, target: str) -> dict[str, Any]:
    """A person moves this folder to the base branch (when nothing set-up-related is left uncommitted)."""
    if left := setup_files(root):
        raise state.StateError(f"Commit these first: {', '.join(left[:6])}")
    branch = gitops.git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    gitops.git(root, "checkout", "--quiet", target)
    return {"branch": target, "from": branch}


def suggest_spec(root: Path, cfg: dict[str, Any], item: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    """Draft the person-written spec sections with one read-only Claude Code call, for a person to review.
    Nothing is written: the dashboard shows the draft in the form and the person saves it."""
    import json
    import os
    import subprocess

    form = spec_form(root, item)
    names = [s["name"] for s in form["sections"]]
    hints = "\n".join(f"- {s['name']}: {s['hint']}" + (" One outcome per line." if s["kind"] == "checklist" else "")
                      for s in form["sections"])
    spec = (root / form["file"]).read_text() if (root / form["file"]).exists() else ""
    prompt = (
        f"You help a developer fill in the spec of a {form['type']} work item before AI agents work on it.\n"
        f"Work item {item['id']}: {item['title']}\n\nCurrent spec file ({form['file']}):\n{spec}\n\n"
        "Look at this repository (read-only: dependency files such as pyproject.toml, package.json, lock files, "
        "the code and tests that matter) so the draft is specific: real versions, files, commands.\n"
        f"Draft these sections:\n{hints}\n\n"
        "Reply with ONLY a JSON object whose keys are exactly these section names: "
        + json.dumps(names) + ". Values are plain text (no markdown headings). Keep each section short and "
        "checkable. For checklists, put one outcome per line without bullets. Do not invent requirements the "
        "request does not imply; say what you assumed in Notes.")
    binary = os.environ.get("SDLC_CLAUDE_BIN", "claude")
    env = {**os.environ}
    if os.environ.get("SDLC_RUN_AUTH") == "login":
        env.pop("ANTHROPIC_API_KEY", None)
    model = os.environ.get("SDLC_SUGGEST_MODEL") or "sonnet"
    try:
        r = subprocess.run([binary, "-p", prompt, "--output-format", "json", "--model", model,
                            "--allowedTools", "Read", "Grep", "Glob", "--permission-mode", "default"],
                           cwd=root, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise state.StateError(f"Could not get a suggestion from Claude Code: {e}") from e
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise state.StateError(f"Claude Code did not answer: {(r.stderr or r.stdout).strip()[-300:]}") from e
    if out.get("is_error"):
        raise state.StateError(f"Claude Code: {out.get('result')}")
    text = str(out.get("result") or "")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        values = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        values = {}
    values = {k: (v if isinstance(v, str) else "\n".join(map(str, v)) if isinstance(v, list) else str(v))
              for k, v in values.items() if k in names}
    if not values:
        raise state.StateError("The suggestion could not be read; fill in the form or try again")
    return {"values": values, "cost_usd": out.get("total_cost_usd"), "model": model}


def reset_tasks(root: Path, req: str) -> dict[str, Any]:
    """A person puts tasks left "in progress" by an ended run back to ready (attempts are kept)."""
    from . import events

    reset = []
    with state.edit(root, req) as item:
        for t in item["tasks"]:
            if t["status"] in (state.T_RUNNING, state.T_REVIEW):
                t["status"] = state.T_FAILED
                t["log"].append({"ts": events.now(), "action": "reset", "note": "no agent was working on it; ready again"})
                reset.append(t["id"])
    return {"reset": reset}


def bad_commits(root: Path, req: str) -> list[dict[str, str]]:
    """Commits on the work-item branch whose title lacks the ID, and how to fix each: drop the ones that only add
    an agent's working copy (a mistake, never project work), give the others the ID."""
    from . import checks

    out = []
    for c in checks.commit_messages(root, req) or []:
        if c["subject"].startswith(req):
            continue
        files = [f for f in gitops.git(root, "show", "--name-only", "--format=", c["sha"], check=False).splitlines() if f]
        junk = files and all(f.startswith(gitops.AGENT_WORKTREES) for f in files)
        out.append({**c, "fix": "drop" if junk else "reword",
                    "why": "it only adds an agent's working copy" if junk else ""})
    return out


def fix_commits(root: Path, req: str) -> dict[str, Any]:
    """A person fixes the branch's commit titles: drops agent-working-copy commits, prefixes the rest with the ID.
    Only for a branch that has not been pushed (rewriting pushed history is left to people on GitHub)."""
    import os
    import sys

    bad = bad_commits(root, req)
    if not bad:
        return {"dropped": [], "reworded": []}
    if any(gitops.git(root, "branch", "-r", "--contains", b["sha"], check=False).strip() for b in bad):
        raise state.StateError("These commits are already pushed. Fix them by hand (git rebase -i) and force-push "
                               "only if nobody else uses the branch.")
    # An agent working copy recorded by mistake and since removed shows as deleted: put back the empty folder git
    # expects, so it is not a change (the commit that added it is dropped below). Other changes are set aside by
    # --autostash and put back afterwards.
    for line in gitops.git(root, "status", "--porcelain", "--untracked-files=no").splitlines():
        path = line[3:]
        if line[:2].strip() == "D" and path.startswith(gitops.AGENT_WORKTREES):
            (root / path).mkdir(parents=True, exist_ok=True)
    from . import checks

    base = checks._base(root)
    drop = {b["sha"] for b in bad if b["fix"] == "drop"}
    reword = {b["sha"] for b in bad if b["fix"] == "reword"}
    todo = (
        "import sys,re\n"
        f"drop={sorted(drop)!r}; reword={sorted(reword)!r}\n"
        "p=sys.argv[1]; out=[]\n"
        "for ln in open(p).read().splitlines():\n"
        "    m=re.match(r'pick ([0-9a-f]+)', ln)\n"
        "    if m and any(m.group(1).startswith(s) or s.startswith(m.group(1)) for s in drop): ln='drop'+ln[4:]\n"
        "    elif m and any(m.group(1).startswith(s) or s.startswith(m.group(1)) for s in reword): ln='reword'+ln[4:]\n"
        "    out.append(ln)\n"
        "open(p,'w').write(chr(10).join(out)+chr(10))\n")
    msg = (
        "import sys\n"
        "p=sys.argv[1]; t=open(p).read()\n"
        f"open(p,'w').write(t if t.startswith({req!r}) else {req + ': '!r}+t)\n")
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="sdlc-fix-") as tmp:  # git runs these as its "editors"
        (Path(tmp) / "todo.py").write_text(todo)
        (Path(tmp) / "msg.py").write_text(msg)
        env = {**os.environ, "GIT_SEQUENCE_EDITOR": f"{_q(sys.executable)} {_q(str(Path(tmp) / 'todo.py'))}",
               "GIT_EDITOR": f"{_q(sys.executable)} {_q(str(Path(tmp) / 'msg.py'))}"}
        r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "rebase", "-i", "--rebase-merges", "--autostash", base],
                           cwd=root, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run(["git", "rebase", "--abort"], cwd=root, capture_output=True)
        raise state.StateError(f"Could not fix the commits (nothing changed): {(r.stderr or r.stdout).strip()[-300:]}")
    return {"dropped": sorted(drop), "reworded": sorted(reword)}


def _q(code: str) -> str:
    import shlex

    return shlex.quote(code)


def record_decision(root: Path, req: str, accepted: bool, note: str, by: str) -> dict[str, Any]:
    """A person answers what the agents asked: the decision goes into the spec's Decisions section, which every
    agent reads, and the request is marked as handled."""
    from . import events

    asked = _agent_help(root, req) or ""
    if not note.strip() and not accepted:
        raise state.StateError("Say what the agents should do instead")
    p = root / "specs" / f"{req}.md"
    text = p.read_text() if p.exists() else f"# {req}\n"
    when = events.now()[:16].replace("T", " ")
    line = (f"- {when} · {by}: **{'Accepted' if accepted else 'Not accepted'}**"
            + (f": {note.strip()}" if note.strip() else "") + (f"  \n  _Agents asked:_ {asked}" if asked else ""))
    if re.search(r"^## Decisions\s*$", text, re.M):
        text = re.sub(r"(^## Decisions\s*\n(?:<!--.*?-->\s*\n)?)", lambda m: m.group(1) + line + "\n", text, count=1,
                      flags=re.M | re.S)
    else:
        text = text.rstrip() + "\n\n## Decisions\n<!-- Decisions people made when the agents asked. Agents treat them as "\
               "instructions and as evidence (an accepted gap is not a failure). -->\n" + line + "\n"
    p.write_text(text)
    events.emit(root, "decision_recorded", req=req, by=by, detail=("accepted" if accepted else "not accepted")
                + (f": {note.strip()}" if note.strip() else ""))
    events.emit(root, "help_handled", req=req, by=by, detail="answered with a decision")
    return {"ok": True, "spec": str(p.relative_to(root)), "line": line}
