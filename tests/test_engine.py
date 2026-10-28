import json
from pathlib import Path

import pytest

from agentic_sdlc import config, design, detect, events, generate, state
from agentic_sdlc.cli import main


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    answers = {"project": {"name": "demo", "type": "new"},
               "stack": {"languages": ["python"], "commands": {"test": "pytest", "lint": "ruff check ."}},
               "stages": {"development": {"parallel_coders": 2}}}
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def cfg(root: Path) -> dict:
    return config.load(root)


PLAN = [{"id": "T1", "title": "migration"}, {"id": "T2", "title": "api"},
        {"id": "T3", "title": "ui"}, {"id": "T4", "title": "e2e", "deps": ["T1", "T2", "T3"]}]


def to_development(root: Path) -> str:
    req = state.new_item(root, cfg(root), "Refund API")["id"]
    for stage in ("requirements", "design", "planning"):
        state.start_stage(root, req)
        if stage == "planning":
            state.set_plan(root, req, PLAN)
        state.complete_stage(root, req, cfg(root))
        state.approve(root, req, cfg(root), "lead")
    state.start_stage(root, req)
    return req


def test_init_scaffolds_repo(repo: Path) -> None:
    assert (repo / "sdlc.config.yaml").exists()
    for p in ("specs/TEMPLATE.md", "docs/adr/TEMPLATE.md", "docs/rca/TEMPLATE.md", ".sdlc/.gitignore"):
        assert (repo / p).exists(), p
    md = (repo / "CLAUDE.md").read_text()
    assert "agentic-sdlc:start" in md and "`pytest`" in md
    allow = json.loads((repo / ".claude/settings.json").read_text())["permissions"]["allow"]
    assert "Bash(sdlc:*)" in allow and "Bash(pytest:*)" in allow


def test_reinit_replaces_section_and_keeps_user_text(repo: Path) -> None:
    md = repo / "CLAUDE.md"
    md.write_text("# Mine\n\nKeep this.\n\n" + md.read_text().split("\n", 2)[2])
    c = cfg(repo)
    c["stack"]["commands"]["test"] = "pytest -q"
    generate.write_claude_md(repo, c)
    text = md.read_text()
    assert "Keep this." in text and "`pytest -q`" in text and text.count("agentic-sdlc:start") == 1


def test_gates_and_stage_flow(repo: Path) -> None:
    c = cfg(repo)
    req = state.new_item(repo, c, "Refund API")["id"]
    assert req == "REQ-001"
    state.start_stage(repo, req)
    assert state.complete_stage(repo, req, c, "spec done") == state.AWAITING
    assert state.next_action(repo, c, req)["action"] == "await_gate"
    with pytest.raises(state.StateError):
        state.start_stage(repo, req)  # cannot restart while waiting at the gate
    state.reject(repo, req, "pm", "Add refund limits")
    nxt = state.next_action(repo, c, req)
    assert nxt["action"] == "run_stage" and nxt["feedback"] == "Add refund limits"
    state.complete_stage(repo, req, c)
    assert state.approve(repo, req, c, "pm") == "design"


def test_parallel_cap_and_dag(repo: Path) -> None:
    req = to_development(repo)
    c = cfg(repo)
    item = state.load(repo, req)
    assert [t["id"] for t in state.ready_tasks(item, c)] == ["T1", "T2"]
    state.update_task(repo, req, c, "T1", "start")
    state.update_task(repo, req, c, "T2", "start")
    with pytest.raises(state.StateError, match="parallel_coders"):
        state.update_task(repo, req, c, "T3", "start")
    with pytest.raises(state.StateError, match="waits on"):
        state.update_task(repo, req, c, "T4", "start")
    for t in ("T1", "T2"):
        state.update_task(repo, req, c, t, "done")
    state.update_task(repo, req, c, "T3", "start")
    with pytest.raises(state.StateError, match="not done"):
        state.complete_stage(repo, req, c)
    state.update_task(repo, req, c, "T3", "done")
    state.update_task(repo, req, c, "T4", "start")
    state.update_task(repo, req, c, "T4", "done")
    assert state.next_action(repo, c, req)["action"] == "complete_stage"
    with pytest.raises(state.StateError, match="sdlc check"):
        state.complete_stage(repo, req, c)
    state.record_checks(repo, req, {"ts": events.now(), "head": "", "passed": True, "results": []})
    assert state.complete_stage(repo, req, c) == state.AWAITING


def test_task_blocks_after_max_attempts(repo: Path) -> None:
    req = to_development(repo)
    c = cfg(repo)
    for _ in range(2):
        state.update_task(repo, req, c, "T1", "start")
        assert state.update_task(repo, req, c, "T1", "fail") == state.T_FAILED
    state.update_task(repo, req, c, "T1", "start")
    assert state.update_task(repo, req, c, "T1", "fail") == state.T_BLOCKED
    assert state.next_action(repo, c, req)["action"] == "human_needed"
    state.update_task(repo, req, c, "T1", "unblock")
    assert state.next_action(repo, c, req)["action"] == "run_stage"


def test_plan_rejects_cycles_and_unknown_deps(repo: Path) -> None:
    req = state.new_item(repo, cfg(repo), "x")["id"]
    with pytest.raises(state.StateError, match="cycle"):
        state.set_plan(repo, req, [{"id": "A", "title": "a", "deps": ["B"]}, {"id": "B", "title": "b", "deps": ["A"]}])
    with pytest.raises(state.StateError, match="unknown"):
        state.set_plan(repo, req, [{"id": "A", "title": "a", "deps": ["Z"]}])


def test_disabled_stages_are_skipped(repo: Path) -> None:
    c = cfg(repo)
    c["stages"]["design"]["enabled"] = False
    c["stages"]["planning"]["enabled"] = False
    req = state.new_item(repo, c, "x")["id"]
    state.start_stage(repo, req)
    state.complete_stage(repo, req, c)
    assert state.approve(repo, req, c, "pm") == "development"


def test_unautomated_stage_is_manual_and_finishes(repo: Path) -> None:
    c = cfg(repo)
    for s in ("requirements", "design", "planning", "development", "qa", "release", "production"):
        c["stages"][s]["enabled"] = False
    c["stages"]["cicd"]["enabled"] = True  # off by default since the pipeline is per project
    c["stages"]["monitoring"]["enabled"] = True  # off by default: out of scope for now
    req = state.new_item(repo, c, "x")["id"]
    assert state.next_action(repo, c, req)["action"] == "manual"  # cicd
    state.complete_stage(repo, req, c)  # cicd has no gate → advances
    state.skip_stage(repo, req, c, "no monitoring yet")
    assert state.load(repo, req)["status"] == "done"


def test_events_written(repo: Path) -> None:
    to_development(repo)
    kinds = [e["event"] for e in events.read(repo)]
    for k in ("item_created", "stage_started", "gate_waiting", "gate_approved", "plan_set"):
        assert k in kinds


def test_invalid_config_rejected() -> None:
    with pytest.raises(config.ConfigError):
        config.build_config({"stages": {"development": {"autonomy": "L9"}}})
    with pytest.raises(config.ConfigError):
        config.build_config({"stages": {"nope": {}}})


def test_detect_python_uv(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["fastapi"]\n[dependency-groups]\ndev=["pytest","ruff"]\n')
    (tmp_path / "uv.lock").write_text("")
    d = detect.detect(tmp_path)
    assert d["languages"] == ["python"] and "fastapi" in d["frameworks"]
    assert d["commands"]["test"] == "uv run pytest" and d["suggested_type"] == "new"


def test_project_templates_override_defaults(repo: Path) -> None:
    adr = repo / "docs" / "adr" / "TEMPLATE.md"
    assert adr.read_text() == generate.template("adr.md")  # init copies the default once
    adr.write_text("# ADR-{number} {title} ({req})\n\n## Compliance impact\n")
    made = design.new_adr(repo, "REQ-001", "Use SQLite")
    assert made.name == "0001-use-sqlite.md"
    assert made.read_text() == "# ADR-0001 Use SQLite (REQ-001)\n\n## Compliance impact\n"
    generate.scaffold(repo, cfg(repo))  # re-running init keeps the project's edits
    assert "Compliance impact" in adr.read_text()
    (repo / "specs" / "TEMPLATE.md").unlink()  # no project copy: the packaged default is used
    assert "REQ-002" in generate.new_spec(repo, "REQ-002", "Payouts").read_text()
