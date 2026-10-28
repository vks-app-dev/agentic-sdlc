import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_sdlc import config, events, state
from agentic_sdlc import human
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.server import create_app


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}}))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def cfg(root: Path) -> dict:
    return config.load(root)


def gate(root: Path, req: str) -> str | None:
    """Run the current stage with no agents and approve its gate."""
    state.start_stage(root, req)
    if state.complete_stage(root, req, cfg(root)) == state.AWAITING:
        return state.approve(root, req, cfg(root), "lead")
    return state.load(root, req)["current_stage"]


PLAN = {"req": "REQ-001", "tasks": [
    {"id": "T1", "title": "migration", "area": "database", "size": "S", "acceptance": ["AC-3"]},
    {"id": "T2", "title": "service", "area": "backend", "size": "M", "deps": ["T1"], "acceptance": ["AC-1", "SEC-1"]},
    {"id": "T3", "title": "metrics", "area": "devops", "size": "S", "acceptance": ["OPS-1"]},
    {"id": "T4", "title": "api", "area": "api", "size": "M", "deps": ["T2", "T3"], "acceptance": ["AC-2"]}]}


def test_planning_sits_between_design_and_development(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["new", "Refunds"]) == 0
    assert gate(repo, "REQ-001") == "design"
    assert gate(repo, "REQ-001") == "planning"
    nxt = state.next_action(repo, cfg(repo), "REQ-001")
    assert [(s["agent"], s["parallel_group"]) for s in nxt["steps"]] == [
        ("plan-test-planner", 0), ("plan-release-planner", 0), ("plan-task-planner", 1)]
    assert nxt["gate"] == ["tech-lead"]
    assert main(["start", "REQ-001"]) == 0
    pack = repo / "docs" / "plan" / "REQ-001"
    assert sorted(p.name for p in pack.iterdir()) == ["README.md", "release-plan.md", "test-plan.md"]
    assert "## Environment readiness" in (pack / "release-plan.md").read_text()
    assert "docs/plan/REQ-001/test-plan.md" in state.next_action(repo, cfg(repo), "REQ-001")["inputs"]
    (repo / ".sdlc" / "plans" / "REQ-001.json").write_text(json.dumps(PLAN))
    capsys.readouterr()
    assert main(["plan", "REQ-001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["waves"] == [["T1", "T3"], ["T2"], ["T4"]] and out["critical_path_length"] == 3
    assert state.load(repo, "REQ-001")["tasks"][1]["area"] == "backend"
    state.complete_stage(repo, "REQ-001", cfg(repo))
    assert state.approve(repo, "REQ-001", cfg(repo), "lead") == "development"


def test_small_change_skips_design_and_planning(repo: Path) -> None:
    assert main(["new", "Rename field", "--type", "change"]) == 0
    state.start_stage(repo, "REQ-001")
    state.set_size(repo, "REQ-001", "S")
    state.complete_stage(repo, "REQ-001", cfg(repo))
    assert state.approve(repo, "REQ-001", cfg(repo), "lead") == "development"
    it = state.load(repo, "REQ-001")
    assert it["stages"]["planning"]["status"] == "skipped" and "Planning only when" in it["stages"]["planning"]["summary"]


def test_items_made_before_planning_get_the_stage(repo: Path) -> None:
    for req, cur in (("REQ-001", "development"), ("REQ-002", "design")):
        assert main(["new", "Old"]) == 0
        p = repo / ".sdlc" / "state" / f"{req}.json"
        raw = json.loads(p.read_text())
        del raw["stages"]["planning"]
        raw["current_stage"] = cur
        p.write_text(json.dumps(raw))
    assert state.load(repo, "REQ-001")["stages"]["planning"]["status"] == "skipped"
    later = state.load(repo, "REQ-002")
    assert later["stages"]["planning"]["status"] == "pending"
    assert list(later["stages"])[:4] == ["requirements", "design", "planning", "development"]


def test_dependencies_between_items(repo: Path) -> None:
    c = cfg(repo)
    c["stages"]["design"]["enabled"] = c["stages"]["planning"]["enabled"] = False
    config.write(repo, c)
    assert main(["new", "Payments core"]) == 0
    assert main(["new", "Refunds", "--blocked-by", "REQ-001"]) == 0
    assert main(["new", "Reports"]) == 0
    assert main(["depends", "REQ-003", "--on", "REQ-002"]) == 0
    with pytest.raises(state.StateError, match="cycle"):
        state.depend(repo, "REQ-001", "REQ-003")
    with pytest.raises(state.StateError, match="itself"):
        state.depend(repo, "REQ-001", "REQ-001")
    assert gate(repo, "REQ-002") == "development"
    nxt = state.next_action(repo, cfg(repo), "REQ-002")
    assert nxt["action"] == "waiting" and nxt["blocked_by"] == ["REQ-001"]
    with pytest.raises(state.StateError, match="waits for REQ-001"):
        state.start_stage(repo, "REQ-002")
    assert TestClient(create_app(repo), headers=human.browser_headers()).post("/api/items/REQ-002/run").status_code == 409
    assert gate(repo, "REQ-001") == "development"
    state.start_stage(repo, "REQ-001")
    state.update_task(repo, "REQ-001", cfg(repo), "T1", "start")
    state.update_task(repo, "REQ-001", cfg(repo), "T1", "done")
    state.record_checks(repo, "REQ-001", {"ts": events.now(), "head": "", "passed": True, "results": []})
    state.complete_stage(repo, "REQ-001", cfg(repo))
    state.approve(repo, "REQ-001", cfg(repo), "lead")
    assert state.next_action(repo, cfg(repo), "REQ-002")["action"] == "run_stage"
    state.start_stage(repo, "REQ-002")


def test_waves() -> None:
    assert state.waves(PLAN["tasks"]) == [["T1", "T3"], ["T2"], ["T4"]]
    assert state.waves([]) == []
