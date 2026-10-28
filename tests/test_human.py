import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_sdlc import config, human, state
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.server import create_app


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}}))
    assert main(["init", "--answers", "answers.json"]) == 0
    assert main(["new", "Refunds"]) == 0
    state.start_stage(tmp_path, "REQ-001")
    state.complete_stage(tmp_path, "REQ-001", config.load(tmp_path))
    return tmp_path


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No terminal: an agent's shell tool, a pipe or a slash command, under any AI tool."""
    monkeypatch.setattr(human, "interactive", lambda: False)


def stage(root: Path) -> str:
    return state.load(root, "REQ-001")["stages"]["requirements"]["status"]


def test_agent_approval_changes_nothing_and_files_a_request(repo: Path, agent: None,
                                                            capsys: pytest.CaptureFixture) -> None:
    assert main(["approve", "REQ-001", "looks", "good"]) == 3
    assert "human decision" in capsys.readouterr().err
    assert stage(repo) == "awaiting_approval"  # nothing changed
    [p] = human.pending(repo)
    assert p["action"] == "approve" and p["req"] == "REQ-001" and "Approve the requirements gate" in p["summary"]
    for cmd in (["reject", "REQ-001", "redo"], ["skip", "REQ-001", "-m", "later"],
                ["retype", "REQ-001", "bug", "it", "is", "a", "bug"]):
        assert main(cmd) == 3
    assert len(human.pending(repo)) == 4 and stage(repo) == "awaiting_approval"
    assert main(["confirm", p["id"]]) == 1  # confirming needs a person too
    assert len(human.pending(repo)) == 4


def test_person_confirms_in_terminal(repo: Path, agent: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert main(["approve", "REQ-001"]) == 3
    [p] = human.pending(repo)
    monkeypatch.setattr(human, "interactive", lambda: True)
    monkeypatch.setattr(human, "confirm_in_terminal", lambda summary, req, yes=False: False)  # typed the wrong id
    assert main(["confirm", p["id"]]) == 1 and len(human.pending(repo)) == 1
    monkeypatch.setattr(human, "confirm_in_terminal", lambda summary, req, yes=False: True)
    assert main(["confirm", p["id"], "--by", "ana"]) == 0
    assert human.pending(repo) == [] and stage(repo) == "approved"
    assert state.load(repo, "REQ-001")["stages"]["requirements"]["decision"]["by"] == "ana"


def test_person_confirms_or_dismisses_in_dashboard(repo: Path, agent: None) -> None:
    assert main(["approve", "REQ-001"]) == 3
    assert main(["reject", "REQ-001", "redo"]) == 3
    approve, reject = sorted(human.pending(repo), key=lambda p: p["action"])
    browser = TestClient(create_app(repo), headers=human.browser_headers())
    assert [p["id"] for p in browser.get("/api/pending").json()] == sorted([approve["id"], reject["id"]])
    assert browser.post(f"/api/pending/{reject['id']}/dismiss").json()["ok"]
    assert browser.post(f"/api/pending/{approve['id']}/confirm", json={"by": "ana"}).json()["ok"]
    assert stage(repo) == "approved" and human.pending(repo) == []


def test_agents_cannot_use_the_dashboard_actions(repo: Path) -> None:
    curl = TestClient(create_app(repo))  # no page token, no browser same-origin header
    assert curl.post("/api/items/REQ-001/approve", json={}).status_code == 403
    assert curl.post("/api/items/REQ-001/run").status_code == 403
    token_only = TestClient(create_app(repo), headers={"X-SDLC-Human": human.dashboard_token()})
    assert token_only.post("/api/items/REQ-001/approve", json={}).status_code == 403
    assert curl.get("/api/items/REQ-001").status_code == 200  # reading is fine
    browser = TestClient(create_app(repo), headers=human.browser_headers())
    assert browser.post("/api/items/REQ-001/approve", json={"by": "ana"}).json()["next_stage"] == "design"


def test_dashboard_page_carries_the_token(repo: Path) -> None:
    from agentic_sdlc.dashboard.server import STATIC
    if not (STATIC / "index.html").exists():
        pytest.skip("dashboard UI not built")
    page = TestClient(create_app(repo)).get("/").text
    assert f'<meta name="sdlc-human" content="{human.dashboard_token()}">' in page


def test_headless_agent_runs_are_never_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the real check, not the conftest stand-in
    monkeypatch.setenv("SDLC_DASHBOARD_RUN", "REQ-001-20261027")
    assert human.interactive() is False


def test_other_human_actions_go_pending(repo: Path, agent: None) -> None:
    state.add_defect(repo, "REQ-001", "Slow list", "low", False, [], "qa-performance-tester")
    assert main(["defect", "REQ-001", "D1", "defer", "-m", "next release"]) == 3
    assert state.load(repo, "REQ-001")["defects"][0]["status"] == "open"
    assert main(["defect", "REQ-001", "D1", "reopen", "-m", "still slow"]) == 0  # testers may reopen
    assert main(["deployed", "REQ-001", "production"]) == 3
    assert "deployments" not in state.load(repo, "REQ-001")
    assert {p["action"] for p in human.pending(repo)} == {"defect-decision", "deployed"}
