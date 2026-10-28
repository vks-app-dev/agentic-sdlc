import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agentic_sdlc import config, state
from agentic_sdlc import human
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.server import create_app


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "existing"}}))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def cfg(root: Path) -> dict:
    return config.load(root)


def new(*args: str) -> str:
    assert main(["new", *args]) == 0
    return state.list_ids(Path.cwd())[-1]


def statuses(root: Path, req: str) -> dict[str, str]:
    it = state.load(root, req)
    return {s: it["stages"][s]["status"] for s in ("requirements", "design", "development")}


def test_feature_is_the_default_and_old_items_read_as_features(repo: Path) -> None:
    req = new("Refunds")
    assert state.load(repo, req)["type"] == "feature"
    assert statuses(repo, req) == {"requirements": "pending", "design": "pending", "development": "pending"}
    p = repo / ".sdlc" / "state" / f"{req}.json"
    old = json.loads(p.read_text())
    del old["type"], old["links"], old["size"]
    p.write_text(json.dumps(old))
    assert state.next_action(repo, cfg(repo), req)["type"] == "feature"


def test_bug_runs_triage_without_gate_then_one_task(repo: Path, capsys: pytest.CaptureFixture) -> None:
    feat = new("Record expenses")
    bug = new("List returns 500 on empty DB", "--type", "bug", "--fixes", feat)
    spec = (repo / "specs" / f"{bug}.md").read_text()
    assert "## Steps to reproduce" in spec and f"Fixes: {feat}" in spec
    assert statuses(repo, bug) == {"requirements": "pending", "design": "skipped", "development": "pending"}
    nxt = state.next_action(repo, cfg(repo), bug)
    assert nxt["stage_title"] == "Triage" and [s["agent"] for s in nxt["steps"]] == ["req-bug-triager"]
    assert f"specs/{feat}.md" in nxt["inputs"] and nxt["links"] == [{"rel": "fixes", "req": feat}]
    state.start_stage(repo, bug)
    assert state.complete_stage(repo, bug, cfg(repo)) == state.APPROVED  # no gate on triage
    assert state.load(repo, bug)["current_stage"] == "development"
    state.start_stage(repo, bug)
    tasks = state.load(repo, bug)["tasks"]
    assert [(t["id"], t["title"]) for t in tasks] == [("T1", "List returns 500 on empty DB")]
    assert state.update_task(repo, bug, cfg(repo), "T1", "start") == state.T_RUNNING


def test_small_change_skips_design_and_large_change_runs_it(repo: Path) -> None:
    feat = new("Record expenses")
    small = new("Rename field note to memo", "--type", "change", "--updates", feat)
    large = new("Expenses become editable", "--type", "change", "--updates", feat)
    assert "## 2. Current behaviour" in (repo / "specs" / f"{small}.md").read_text()
    agents = [s["agent"] for s in state.next_action(repo, cfg(repo), small)["steps"]]
    assert agents == ["req-change-impact-analyst", "req-analyst", "req-nfr-analyst", "req-ambiguity-checker"]
    for req, size in ((small, "S"), (large, "L")):
        state.start_stage(repo, req)
        assert main(["size", req, size.lower()]) == 0
        state.complete_stage(repo, req, cfg(repo))
        state.approve(repo, req, cfg(repo), "ana")
    assert state.load(repo, small)["current_stage"] == "development"
    assert "size S" in state.load(repo, small)["stages"]["design"]["summary"]
    assert state.load(repo, large)["current_stage"] == "design"
    state.start_stage(repo, large)
    with pytest.raises(state.StateError, match="Design has already started"):
        state.set_size(repo, large, "S")


def test_task_goes_straight_to_development(repo: Path) -> None:
    req = new("Upgrade FastAPI", "--type", "task")
    it = state.load(repo, req)
    assert it["current_stage"] == "development"
    assert it["stages"]["development"]["artifacts"] == [f"specs/{req}.md"]
    assert "## Done when" in (repo / "specs" / f"{req}.md").read_text()


def test_links_must_point_at_known_items(repo: Path) -> None:
    assert main(["new", "Fix it", "--type", "bug", "--fixes", "REQ-042"]) == 1
    assert state.list_ids(repo) == []


def test_retype_restarts_and_keeps_old_spec(repo: Path) -> None:
    bug = new("Totals should include tax", "--type", "bug")
    spec = repo / "specs" / f"{bug}.md"
    spec.write_text(spec.read_text().replace("## Report\n", "## Report\nTotals exclude tax.\n"))
    state.start_stage(repo, bug)
    assert main(["retype", bug, "change"]) == 1  # a reason is required
    assert main(["retype", bug, "change", "works", "as", "specified"]) == 0
    it = state.load(repo, bug)
    assert it["type"] == "change" and it["current_stage"] == "requirements"
    assert it["stages"]["requirements"]["status"] == "pending" and it["stages"]["design"]["status"] == "pending"
    text = spec.read_text()
    assert text.startswith(f"# {bug}") and "## 2. Current behaviour" in text
    assert "## Earlier notes (written when this was a bug)" in text and "Totals exclude tax." in text
    assert it["history"][-1]["what"] == "retyped" and it["history"][-1]["comment"] == "works as specified"


def test_retype_refused_once_development_started(repo: Path) -> None:
    req = new("Upgrade FastAPI", "--type", "task")
    state.start_stage(repo, req)
    with pytest.raises(state.StateError, match="Development has started"):
        state.retype(repo, req, cfg(repo), "change", "ana", "it changes behaviour")


def test_config_overrides_and_validation(repo: Path) -> None:
    raw = yaml.safe_load((repo / "sdlc.config.yaml").read_text())
    assert raw["work_types"]["bug"] == {"stages": ["requirements", "development"], "gates": {"requirements": []}}
    raw["work_types"]["bug"]["gates"]["requirements"] = ["tech-lead"]
    (repo / "sdlc.config.yaml").write_text(yaml.safe_dump(raw))
    bug = new("Crash on save", "--type", "bug")
    assert state.load(repo, bug)["stages"]["requirements"]["gate"] == ["tech-lead"]
    raw["work_types"]["epic"] = {}
    raw["work_types"]["task"] = {"stages": ["qa"]}
    with pytest.raises(config.ConfigError) as e:
        config.validate(raw)
    assert "unknown work type 'epic'" in str(e.value) and "work_types.task.stages" in str(e.value)


def test_project_templates_per_type(repo: Path) -> None:
    own = repo / "specs" / "TEMPLATE-bug.md"
    assert own.exists()
    own.write_text("# {req}: {title}\n\n## Customer impact\n")
    req = new("Crash", "--type", "bug")
    assert (repo / "specs" / f"{req}.md").read_text() == f"# {req}: Crash\n\n## Customer impact\n"


def test_dashboard_types_retype_and_metrics(repo: Path) -> None:
    client = TestClient(create_app(repo), headers=human.browser_headers())
    bug = new("Totals wrong", "--type", "bug")
    new("Upgrade FastAPI", "--type", "task")
    project = client.get("/api/project").json()
    bug_type = next(w for w in project["work_types"] if w["name"] == "bug")
    assert bug_type["stage_titles"] == {"requirements": "Triage"} and bug_type["agents"]["requirements"] == ["req-bug-triager"]
    detail = client.get(f"/api/items/{bug}").json()
    assert detail["type"] == "bug" and detail["can_retype"]
    assert client.post(f"/api/items/{bug}/retype", json={"type": "change"}).status_code == 422
    assert client.post(f"/api/items/{bug}/retype", json={"type": "change", "comment": "as specified"}).json()["ok"]
    assert client.get(f"/api/items/{bug}").json()["type"] == "change"
    by_type = {t["type"]: t for t in client.get("/api/metrics").json()["by_type"]}
    assert by_type["change"]["open"] == 1 and by_type["task"]["total"] == 1 and by_type["bug"]["total"] == 0
