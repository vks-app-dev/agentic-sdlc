import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_sdlc import blockers, config, human, state
from agentic_sdlc.cli import new_work
from agentic_sdlc.dashboard.server import create_app
from agentic_sdlc.cli import main


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root, capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Like the sample project: main, an earlier item's branch checked out, setup changes not committed."""
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "a.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}}))
    main(["init", "--answers", "a.json"])
    (tmp_path / "a.json").unlink()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    git(tmp_path, "checkout", "-q", "-b", "sdlc/REQ-001")
    (tmp_path / "app.py").write_text("print('REQ-001 work')\n")
    git(tmp_path, "commit", "-qam", "REQ-001 work")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "staging.sh").write_text("#!/bin/sh\n")
    (tmp_path / ".gitignore").write_text(".staging/\n")
    return tmp_path


def test_blockers_found_fixed_from_the_dashboard(repo: Path) -> None:
    c = config.load(repo)
    state.new_item(repo, c, "Record and list expenses")  # REQ-001, whose branch is checked out
    req = new_work(repo, c, "Upgrade FastAPI", "task", [], "Upgrade FastAPI to the latest version")["req"]
    codes = [b["code"] for b in blockers.find(repo, c, req)]
    assert codes == ["spec", "uncommitted", "other_branch"]
    spec = blockers.find(repo, c, req)[0]
    assert spec["sections"] == ["What and why", "Done when"]

    client = TestClient(create_app(repo), headers=human.browser_headers())
    inbox = client.get("/api/blockers").json()
    assert inbox[0]["req"] == req and len(inbox[0]["blockers"]) == 3

    form = client.get(f"/api/items/{req}/spec").json()
    assert form["sections"][0]["value"] == "Upgrade FastAPI to the latest version"  # suggested from the request
    r = client.post(f"/api/items/{req}/spec", json={"values": {
        "What and why": "Upgrade FastAPI 0.110 to 0.115 for the security fix.",
        "Done when": "Existing tests pass\nuv.lock pins fastapi 0.115"}})
    assert r.status_code == 200 and r.json()["gaps"] == []
    text = (repo / "specs" / f"{req}.md").read_text()
    assert "- [ ] uv.lock pins fastapi 0.115" in text and "<!-- The technical work" in text

    commit = next(b for b in client.get(f"/api/items/{req}").json()["blockers"] if b["code"] == "uncommitted")
    assert commit["target"] == "main" and {"scripts/staging.sh", ".gitignore"} <= set(commit["files"])
    done = client.post("/api/git/commit", json={"files": commit["files"], "message": "Project setup",
                                               "target": "main"}).json()
    assert done["switched"] and done["branch"] == "main"
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert "scripts/staging.sh" in git(repo, "show", "--name-only", "main")
    assert "scripts" not in git(repo, "show", "--name-only", "sdlc/REQ-001")  # the other item's branch is untouched
    assert (repo / f"specs/{req}.md").exists()  # the item's own spec came along, still uncommitted
    assert blockers.find(repo, config.load(repo), req) == []
    # people only: an agent calling the API cannot commit
    assert TestClient(create_app(repo)).post("/api/git/commit", json={"files": ["x"], "message": "m",
                                                                      "target": "main"}).status_code == 403


def test_ai_suggests_the_spec_for_a_person_to_review(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = repo / "fake-claude"
    fake.write_text("#!/bin/sh\ncat <<'X'\n" + json.dumps({"type": "result", "total_cost_usd": 0.03, "result":
        'Here you go: {"What and why": "Upgrade FastAPI 0.110.0 to 0.115.6 (pyproject.toml) for the latest fixes.",'
        ' "Done when": ["Existing tests pass", "pyproject.toml and uv.lock pin fastapi 0.115.6"],'
        ' "Risk and rollback": "Risk: low. Rollback: revert the commit.", "Notes": "Assumed no API changes."}'})
        + "\nX\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_CLAUDE_BIN", str(fake))
    c = config.load(repo)
    req = new_work(repo, c, "Upgrade FastAPI", "task", [], "Upgrade FastAPI to the latest version")["req"]
    client = TestClient(create_app(repo), headers=human.browser_headers())
    r = client.post(f"/api/items/{req}/spec/suggest").json()
    assert r["values"]["Done when"] == "Existing tests pass\npyproject.toml and uv.lock pin fastapi 0.115.6"
    assert "0.115.6" in r["values"]["What and why"] and r["cost_usd"] == 0.03
    assert "0.115.6" not in (repo / "specs" / f"{req}.md").read_text()  # nothing is written until a person saves


def test_stuck_worktree_agent_help_and_dependency_commands(repo: Path) -> None:
    from agentic_sdlc import events
    from agentic_sdlc.dashboard.runner import Runner

    git(repo, "checkout", "-q", "main")
    (repo / ".gitignore").write_text(".staging/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "setup")
    c = config.load(repo)
    req = new_work(repo, c, "Upgrade FastAPI", "task", [], "x")["req"]
    blockers.write_spec(repo, state.load(repo, req), {"What and why": "Upgrade.", "Done when": "lock pins 0.115"})
    wt = repo / ".claude" / "worktrees" / "agent-1"
    git(repo, "worktree", "add", "-q", "-b", f"sdlc/{req}-T1", str(wt))
    git(repo, "worktree", "lock", str(wt))
    events.emit(repo, "run_started", req=req)
    events.emit(repo, "human_needed", req=req, detail="Bash was denied for the coder")
    found = {b["code"]: b for b in blockers.find(repo, config.load(repo), req)}
    assert set(found) == {"stuck_worktree", "agent_help"}  # the agents' working copy is not an uncommitted file
    assert found["agent_help"]["blocking"] is False and "denied" in found["agent_help"]["detail"]
    client = TestClient(create_app(repo), headers=human.browser_headers())
    assert client.post(f"/api/items/{req}/free-worktrees").json()["freed"] == [f"sdlc/{req}-T1"]
    assert not wt.exists() and git(repo, "branch", "--list", f"sdlc/{req}-T1").strip()  # the branch is kept
    assert [b["code"] for b in blockers.find(repo, config.load(repo), req)] == ["agent_help"]
    events.emit(repo, "run_started", req=req)  # a new run clears the old request
    assert blockers.find(repo, config.load(repo), req) == []

    raw = config.load(repo)
    raw["stack"]["package_manager"] = "uv"
    assert "Bash(uv lock:*)" in Runner(repo, lambda: raw).command(req)


def test_task_left_in_progress_is_reset_by_a_person(repo: Path) -> None:
    git(repo, "checkout", "-q", "main")
    (repo / ".gitignore").write_text(".staging/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "setup")
    c = config.load(repo)
    req = new_work(repo, c, "Upgrade FastAPI", "task", [], "x")["req"]
    blockers.write_spec(repo, state.load(repo, req), {"What and why": "Upgrade.", "Done when": "lock pins 0.115"})
    state.start_stage(repo, req)
    state.update_task(repo, req, c, "T1", "start")
    assert [b["code"] for b in blockers.find(repo, c, req)] == ["orphan_task"]
    assert blockers.find(repo, c, req, running=True) == []  # while agents run, it is theirs
    client = TestClient(create_app(repo), headers=human.browser_headers())
    assert client.post(f"/api/items/{req}/reset-tasks").json()["reset"] == ["T1"]
    t = state.load(repo, req)["tasks"][0]
    assert t["status"] == "failed" and t["attempts"] == 1 and blockers.find(repo, c, req) == []


def test_agent_help_comes_with_next_steps(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_sdlc import events

    assert blockers.help_steps("Bash access was denied for the coder")["known"]
    unknown = blockers.help_steps("The payments sandbox returned something odd")
    assert not unknown["known"] and "Explain with AI" in unknown["steps"][-1]  # always a next step

    git(repo, "checkout", "-q", "main")
    c = config.load(repo)
    req = new_work(repo, c, "Task", "task", [], "x")["req"]
    events.emit(repo, "run_started", req=req)
    events.emit(repo, "human_needed", req=req, detail="The payments sandbox returned something odd")
    fake = repo / "fake-claude"
    fake.write_text("#!/bin/sh\necho '" + json.dumps({"type": "result", "total_cost_usd": 0.004,
                    "result": '{"steps": ["Check the sandbox key in .env", "Run the agents again"]}'}) + "'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_CLAUDE_BIN", str(fake))
    client = TestClient(create_app(repo), headers=human.browser_headers())
    r = client.post(f"/api/items/{req}/help/explain").json()
    assert r["steps"] == ["Check the sandbox key in .env", "Run the agents again"] and r["cost_usd"] == 0.004


def test_fix_commits_drops_worktree_links_and_adds_the_id(repo: Path) -> None:
    git(repo, "checkout", "-q", "main")
    c = config.load(repo)
    state.new_item(repo, c, "Earlier item")  # REQ-001 owns the fixture's sdlc/REQ-001 branch
    req = new_work(repo, c, "Task", "task", [], "x")["req"]
    git(repo, "checkout", "-q", "-b", f"sdlc/{req}")
    git(repo, "commit", "-q", "--allow-empty", "-m", f"{req}: spec")
    (repo / "notes.txt").write_text("n\n")
    git(repo, "add", "notes.txt")
    git(repo, "commit", "-q", "-m", "Project setup")  # no ID: gets one
    sub = repo / ".claude" / "worktrees" / "agent-9"
    sub.mkdir(parents=True)
    git(sub, "init", "-q")
    git(sub, "commit", "-q", "--allow-empty", "-m", "x")
    git(repo, "add", ".claude/worktrees/agent-9")
    git(repo, "commit", "-q", "-m", "Oops")  # only an agent's working copy: dropped
    git(repo, "commit", "-q", "--allow-empty", "-m", f"{req}/T1: work")
    import shutil
    shutil.rmtree(sub)  # the working copy was freed afterwards: it shows as deleted
    (repo / "notes.txt").write_text("changed, not committed\n")  # and a local change is waiting
    bad = {b["subject"]: b["fix"] for b in blockers.bad_commits(repo, req)}
    assert bad == {"Project setup": "reword", "Oops": "drop"}
    assert "bad_commits" in [b["code"] for b in blockers.find(repo, c, req)]
    client = TestClient(create_app(repo), headers=human.browser_headers())
    r = client.post(f"/api/items/{req}/fix-commits").json()
    assert "dropped" in r, r
    assert len(r["dropped"]) == 1 and len(r["reworded"]) == 1
    subjects = git(repo, "log", "--format=%s", "main..HEAD").splitlines()
    assert subjects == [f"{req}/T1: work", f"{req}: Project setup", f"{req}: spec"]
    assert blockers.bad_commits(repo, req) == []
    assert (repo / "notes.txt").read_text() == "changed, not committed\n"  # the local change is back
    assert ".claude/worktrees" not in git(repo, "ls-files")


def test_a_person_answers_the_agents_question(repo: Path) -> None:
    from agentic_sdlc import events

    git(repo, "checkout", "-q", "main")
    c = config.load(repo)
    req = new_work(repo, c, "Task", "task", [], "x")["req"]
    (repo / "docs" / "release" / req).mkdir(parents=True)
    (repo / "docs" / "release" / req / "readiness.md").write_text("# Readiness\n")
    events.emit(repo, "run_started", req=req)
    events.emit(repo, "human_needed", req=req,
                detail=f"Readiness is FAIL: decide whether to accept the gaps, then update docs/release/{req}/readiness.md")
    note = next(b for b in blockers.find(repo, c, req) if b["code"] == "agent_help")
    assert note["decision"] and note["files"][0] == f"docs/release/{req}/readiness.md"  # what it names comes first
    client = TestClient(create_app(repo), headers=human.browser_headers())
    assert client.post(f"/api/items/{req}/decision", json={"accepted": False, "note": ""}).status_code == 409
    r = client.post(f"/api/items/{req}/decision", json={"accepted": True, "note": "No performance targets for an upgrade"})
    assert r.status_code == 200
    spec = (repo / "specs" / f"{req}.md").read_text()
    assert "## Decisions" in spec and "**Accepted**: No performance targets for an upgrade" in spec
    assert "Agents asked:" in spec and not [b for b in blockers.find(repo, c, req) if b["code"] == "agent_help"]
