import json
from pathlib import Path

import pytest

from agentic_sdlc import checks, config, detect, events, handover, state
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.runner import Runner


def monorepo(root: Path) -> None:
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text(json.dumps({
        "scripts": {"dev": "vite", "build": "vite build", "test": "vitest run", "lint": "eslint ."},
        "dependencies": {"react": "18.3.1"}, "devDependencies": {"@playwright/test": "1.48.0", "typescript": "5.6.3"}}))
    (root / "backend" / "app").mkdir(parents=True)
    (root / "backend" / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["fastapi"]\n[dependency-groups]\ndev=["pytest","ruff"]\n')
    (root / "backend" / "uv.lock").write_text("")
    (root / "backend" / "app" / "main.py").write_text("")


def test_detect_finds_frontend_and_backend(tmp_path: Path) -> None:
    monorepo(tmp_path)
    d = detect.detect(tmp_path)
    comps = d["components"]
    assert set(comps) == {"frontend", "backend"}
    assert comps["frontend"]["kind"] == "frontend" and comps["backend"]["kind"] == "backend"
    assert comps["frontend"]["commands"]["e2e"] == "npx playwright test"
    assert comps["frontend"]["commands"]["run"] == "npm run dev"
    assert comps["backend"]["commands"]["run"] == "uv run uvicorn app.main:app --port 8000"
    assert set(d["languages"]) == {"python", "typescript"}


def test_detect_monorepo_folders_and_single_repos(tmp_path: Path) -> None:
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "package.json").write_text('{"dependencies": {"next": "14.2.0"}}')
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "go.mod").write_text("module api\n")
    (tmp_path / "package.json").write_text('{"workspaces": ["apps/*"]}')
    comps = detect.detect(tmp_path)["components"]
    assert set(comps) == {"apps-web", "services-api"} and comps["services-api"]["kind"] == "backend"
    single = tmp_path / "single"
    single.mkdir()
    (single / "package.json").write_text('{"dependencies": {"express": "4.21.0"}}')
    assert "components" not in detect.detect(single)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monorepo(tmp_path)
    answers = {"project": {"name": "shop", "type": "new"},
               "components": {"backend": {"path": "backend", "kind": "backend",
                                          "commands": {"test": "test -f pyproject.toml", "lint": "true", "run": "x"}},
                              "frontend": {"path": "frontend", "kind": "frontend",
                                           "commands": {"test": "test -f package.json", "e2e": "true", "run": "npm run dev"}}},
               "stages": {s: {"enabled": False} for s in ("design", "planning")}}
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def test_config_components_and_validation(repo: Path) -> None:
    c = config.load(repo)
    assert [(x["name"], x["path"], x["kind"]) for x in config.components(c)] == [
        ("backend", "backend", "backend"), ("frontend", "frontend", "frontend")]
    assert "**frontend** (frontend, run in `frontend/`)" in (repo / "CLAUDE.md").read_text()
    bad = {**c, "components": {"x": {"path": "../outside", "commands": {}}, "y": {"kind": "mobile"}}}
    with pytest.raises(config.ConfigError) as e:
        config.validate(bad)
    assert "components.x.path" in str(e.value) and "components.y.kind" in str(e.value)
    assert config.components({"stack": {"commands": {"test": "pytest"}}}) == [
        {"name": "", "path": ".", "kind": "", "commands": {"test": "pytest"}}]


def test_checks_run_in_each_component_folder(repo: Path) -> None:
    r = checks.run(repo, "REQ-001", config.load(repo))
    names = {x["name"]: x["status"] for x in r["results"]}
    assert names == {"backend: lint": "passed", "backend: test": "passed", "frontend: test": "passed",
                     "frontend: e2e": "passed", "secret scan (built-in)": "passed"}  # `test -f` passes only in its folder
    assert "lint" not in r["not_configured"] and "build" in r["not_configured"]


def test_tasks_carry_their_component_and_ui_is_detected(repo: Path) -> None:
    c = config.load(repo)
    assert main(["new", "Checkout"]) == 0
    state.start_stage(repo, "REQ-001")
    state.complete_stage(repo, "REQ-001", c)
    state.approve(repo, "REQ-001", c, "lead")
    state.set_plan(repo, "REQ-001", [{"id": "T1", "title": "POST /orders", "component": "backend"},
                                      {"id": "T2", "title": "Checkout screen", "component": "frontend", "deps": ["T1"]}])
    state.start_stage(repo, "REQ-001")
    nxt = state.next_action(repo, c, "REQ-001")
    t1 = nxt["ready_tasks"][0]
    assert t1["component"] == "backend" and t1["path"] == "backend" and t1["commands"]["lint"] == "true"
    assert [x["name"] for x in nxt["components"]] == ["backend", "frontend"]
    assert state.has_ui(repo, state.load(repo, "REQ-001"), c)  # a frontend component means a UI
    state.record_checks(repo, "REQ-001", {"ts": events.now(), "head": "", "passed": True, "results": []})
    text = handover.build(repo, "REQ-001", c)
    assert "- **frontend** (frontend, in `frontend/`):" in text and "  - End-to-end tests: `true`" in text


def test_headless_runs_may_use_every_component_command(repo: Path) -> None:
    cmd = Runner(repo, lambda: config.load(repo)).command("REQ-001")
    allowed = cmd[cmd.index("--allowedTools") + 1:]
    for rule in ("Bash(npm run dev:*)", "Bash(test -f package.json:*)", "Bash(cd:*)", "Bash(npm run:*)",
                 "Bash(npx playwright:*)"):
        assert rule in allowed, rule


def test_headless_runs_never_get_production_commands(repo: Path) -> None:
    c = config.load(repo)
    c["environments"] = {"staging": {"deploy": "./deploy.sh staging", "smoke": ["curl -fsS http://stg/health"]},
                         "production": {"deploy": "./deploy.sh production", "rollback": "./rollback.sh production"}}
    config.write(repo, c)
    allowed = Runner(repo, lambda: config.load(repo)).command("REQ-001")
    assert "Bash(./deploy.sh staging:*)" in allowed and "Bash(curl -fsS http://stg/health:*)" in allowed
    assert not any("production" in a for a in allowed)
    guard = json.loads((repo / ".sdlc" / "run" / "guard.json").read_text())
    assert guard["commands"] == ["./deploy.sh production", "./rollback.sh production"]
