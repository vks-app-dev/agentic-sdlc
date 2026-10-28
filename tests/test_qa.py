import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_sdlc import config, events, state
from agentic_sdlc import human
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.server import create_app

GUARD = Path(__file__).parents[1] / "plugin" / "hooks" / "guard.py"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    answers = {"project": {"name": "demo", "type": "new"},
               "stages": {s: {"enabled": False} for s in ("design", "planning", "cicd")}}
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    assert main(["new", "Refunds"]) == 0
    c = config.load(tmp_path)
    state.start_stage(tmp_path, "REQ-001")
    state.complete_stage(tmp_path, "REQ-001", c)
    state.approve(tmp_path, "REQ-001", c, "lead")
    state.start_stage(tmp_path, "REQ-001")
    state.update_task(tmp_path, "REQ-001", c, "T1", "start")
    state.update_task(tmp_path, "REQ-001", c, "T1", "done")
    passed(tmp_path)
    state.complete_stage(tmp_path, "REQ-001", c)
    assert state.approve(tmp_path, "REQ-001", c, "peer") == "qa"
    assert main(["start", "REQ-001"]) == 0
    return tmp_path


def passed(root: Path) -> None:
    state.record_checks(root, "REQ-001", {"ts": events.now(), "head": "abc", "passed": True, "results": []})


def report(root: Path, name: str, verdict: str) -> None:
    p = root / "docs" / "qa" / "REQ-001" / f"{name}.md"
    p.write_text(p.read_text().replace("Verdict: PASS / FAIL / PARTIAL", f"Verdict: {verdict}", 1))


def test_qa_stage_agents_and_reports(repo: Path) -> None:
    nxt = state.next_action(repo, config.load(repo), "REQ-001")
    assert nxt["stage"] == "qa" and nxt["action"] == "run_stage" and nxt["gate"] == ["qa-lead"]
    assert [(s["agent"], s["parallel_group"]) for s in nxt["steps"]] == [
        ("qa-functional-tester", 0), ("qa-security-tester", 0), ("qa-exploratory-tester", 0), ("qa-uat-tester", 0),
        ("qa-performance-tester", 1), ("qa-defect-triager", 2)]
    files = sorted(p.name for p in (repo / "docs" / "qa" / "REQ-001").iterdir())
    assert files == ["exploratory.md", "functional.md", "performance.md", "security.md", "uat.md"]
    assert "docs/dev/REQ-001/handover.md" not in nxt["inputs"]  # only files that exist are listed


def test_blocker_must_be_fixed_and_verified_before_signoff(repo: Path, capsys: pytest.CaptureFixture) -> None:
    c = config.load(repo)
    assert main(["defect", "REQ-001", "add", "Refund", "over", "charge", "accepted", "--severity", "high",
                 "--blocker", "--covers", "AC-3", "--found-by", "qa-functional-tester", "-m", "POST 150 on 100"]) == 0
    assert main(["defect", "REQ-001", "add", "Typo in error message", "--severity", "low"]) == 0
    assert main(["defect", "REQ-001", "add", "Same over-refund", "--severity", "high", "--blocker"]) == 0
    assert main(["defect", "REQ-001", "D3", "duplicate"]) == 1  # needs --of
    assert main(["defect", "REQ-001", "D3", "duplicate", "--of", "D1"]) == 0
    with pytest.raises(state.StateError, match="release-blocking defects still open: D1"):
        state.complete_stage(repo, "REQ-001", c)
    assert main(["defect", "REQ-001", "D1", "fix"]) == 0
    nxt = state.next_action(repo, c, "REQ-001")
    assert nxt["ready_tasks"][0]["id"] == "T2" and nxt["open_blockers"] == ["D1"]
    state.update_task(repo, "REQ-001", c, "T2", "start")
    state.update_task(repo, "REQ-001", c, "T2", "done")
    d1 = next(d for d in state.load(repo, "REQ-001")["defects"] if d["id"] == "D1")
    assert d1["status"] == "fixed" and d1["task"] == "T2" and d1["covers"] == ["AC-3"]
    assert state.next_action(repo, c, "REQ-001")["to_retest"] == ["D1"]
    with pytest.raises(state.StateError, match="merged after"):  # the fix needs a fresh check run
        state.complete_stage(repo, "REQ-001", c)
    passed(repo)
    with pytest.raises(state.StateError, match="open: D1"):  # fixed is not verified
        state.complete_stage(repo, "REQ-001", c)
    assert main(["defect", "REQ-001", "D1", "verified", "-m", "POST 150 now 422"]) == 0
    for name in ("functional", "security", "exploratory", "uat"):
        report(repo, name, "PASS")
    report(repo, "performance", "N/A")
    capsys.readouterr()
    assert main(["test-report", "REQ-001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["unmet"] == ["Every must acceptance criterion has a test"]  # the spec's AC-1 (must) has no test yet
    (repo / "tests").mkdir()
    (repo / "tests" / "test_refunds.py").write_text("def test_refund():  # REQ-001 AC-1\n    pass\n")
    assert main(["test-report", "REQ-001"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["exit_criteria_met"] and out["open_blockers"] == []
    assert main(["complete", "REQ-001"]) == 0
    assert main(["approve", "REQ-001", "ship", "it"]) == 0
    summary = (repo / "docs" / "qa" / "REQ-001" / "summary.md").read_text()
    assert "Exit criteria **met**" in summary and "| UAT | [uat.md](uat.md) | PASS |" in summary
    assert "Signed off by" in summary and ": ship it. Released with open non-blocking defects: D2." in summary
    log = (repo / "docs" / "qa" / "REQ-001" / "defects.md").read_text()
    assert "| D3 | Same over-refund | high | yes | duplicate of D1 |" in log


def test_deferring_and_accepting_are_human_decisions(repo: Path) -> None:
    state.add_defect(repo, "REQ-001", "Slow list on 1M rows", "medium", True, [], "qa-performance-tester")
    with pytest.raises(state.StateError, match="Say why"):
        state.update_defect(repo, "REQ-001", "D1", "defer")
    hook = subprocess.run([sys.executable, str(GUARD)], text=True, capture_output=True, input=json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": "sdlc defect REQ-001 D1 defer -m later"}}))
    assert json.loads(hook.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    client = TestClient(create_app(repo), headers=human.browser_headers())
    assert client.post("/api/items/REQ-001/defects/D1", json={"action": "defer"}).status_code == 422
    r = client.post("/api/items/REQ-001/defects/D1", json={"action": "defer", "comment": "next release", "by": "ana"})
    assert r.json()["defect"]["status"] == "deferred"
    assert state.open_blocker_defects(state.load(repo, "REQ-001")) == []
    assert client.get("/api/items/REQ-001").json()["defects"][0]["log"][-1]["by"] == "ana"


def test_ui_tester_runs_only_for_items_with_a_ui(repo: Path) -> None:
    c = config.load(repo)
    agents = lambda: [s["agent"] for s in state.next_action(repo, config.load(repo), "REQ-001")["steps"]]  # noqa: E731
    assert "qa-ui-tester" not in agents()  # API project, no ux.md
    assert not (repo / "docs" / "qa" / "REQ-001" / "ui.md").exists()
    ux = repo / "docs" / "design" / "REQ-001" / "ux.md"
    ux.parent.mkdir(parents=True)
    ux.write_text("# UI/UX design\n")
    assert "qa-ui-tester" in agents()
    ux.unlink()
    c["stack"]["framework"] = "react"
    config.write(repo, c)
    assert "qa-ui-tester" in agents()
    c["stack"]["ui"] = False  # explicit override wins over the framework
    config.write(repo, c)
    assert "qa-ui-tester" not in agents()


def test_ui_report_counts_only_when_present(repo: Path, capsys: pytest.CaptureFixture) -> None:
    from agentic_sdlc import testreport
    capsys.readouterr()
    assert main(["test-report", "REQ-001"]) == 0
    assert "ui" not in json.loads(capsys.readouterr().out)["verdicts"]
    testreport.new_pack(repo, "REQ-001", "Refunds", ui=True)
    assert "## Accessibility" in (repo / "docs" / "qa" / "REQ-001" / "ui.md").read_text()
    assert main(["test-report", "REQ-001"]) == 0
    assert json.loads(capsys.readouterr().out)["verdicts"]["ui"] == "not filled"


def test_unfilled_report_is_not_a_pass(repo: Path) -> None:
    from agentic_sdlc import testreport
    p = repo / "docs" / "qa" / "REQ-001" / "security.md"
    assert testreport.verdict(p) == "not filled"
    p.write_text("Verdict: N/A — no new entry points\n")
    assert testreport.verdict(p) == "N/A"
