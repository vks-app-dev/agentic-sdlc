import json
import subprocess
from pathlib import Path

import pytest

from agentic_sdlc import config, events, release, state
from agentic_sdlc.cli import main


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.email=a@b", "-c", "user.name=a", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    answers = {"project": {"name": "shop", "type": "new"},
               "stages": {s: {"enabled": False} for s in ("design", "planning", "qa")}}
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    c = config.load(tmp_path)
    c["environments"] = {
        "staging": {"deploy": "echo deployed > .staging", "rollback": "rm -f .staging", "smoke": ["test -f .staging"]},
        "production": {"deploy": "echo prod > .prod", "smoke": ["true"]}}
    config.write(tmp_path, c)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    assert main(["new", "Refunds"]) == 0
    c = config.load(tmp_path)
    state.start_stage(tmp_path, "REQ-001")
    state.complete_stage(tmp_path, "REQ-001", c)
    state.approve(tmp_path, "REQ-001", c, "lead")
    state.start_stage(tmp_path, "REQ-001")
    state.update_task(tmp_path, "REQ-001", c, "T1", "start")
    state.update_task(tmp_path, "REQ-001", c, "T1", "done")
    state.record_checks(tmp_path, "REQ-001", {"ts": events.now(), "head": "", "passed": True, "results": []})
    state.complete_stage(tmp_path, "REQ-001", c)
    assert state.approve(tmp_path, "REQ-001", c, "peer") == "release"  # CI/CD is off by default now
    assert main(["start", "REQ-001"]) == 0
    return tmp_path


def fill(root: Path, name: str, first: str) -> None:
    p = root / "docs" / "release" / "REQ-001" / f"{name}.md"
    lines = p.read_text().splitlines()
    p.write_text("\n".join([first, *lines[1:]]) + "\n")


def test_release_stage_agents_and_pack(repo: Path) -> None:
    nxt = state.next_action(repo, config.load(repo), "REQ-001")
    assert [(s["agent"], s["parallel_group"]) for s in nxt["steps"]] == [
        ("rel-deploy-verifier", 0), ("rel-rollback-rehearser", 0), ("rel-notes-writer", 0), ("rel-readiness-checker", 1)]
    files = sorted(p.name for p in (repo / "docs" / "release" / "REQ-001").iterdir())
    assert files == ["readiness.md", "release-notes.md", "rollback.md", "verification.md"]


def test_staging_is_for_agents_production_for_people(repo: Path, capsys: pytest.CaptureFixture) -> None:
    c = config.load(repo)
    with pytest.raises(state.StateError, match="release evidence not ready"):
        state.complete_stage(repo, "REQ-001", c)
    assert main(["deploy", "REQ-001", "production"]) == 1
    err = capsys.readouterr().err
    assert "deployed by a person" in err and "`echo prod > .prod`" in err and not (repo / ".prod").exists()
    for name in ("readiness", "rollback", "verification"):
        fill(repo, name, "Verdict: PASS")
    with pytest.raises(state.StateError, match="deploy to staging"):
        state.complete_stage(repo, "REQ-001", c)
    assert main(["deploy", "REQ-001", "staging"]) == 0 and (repo / ".staging").exists()
    assert main(["smoke", "REQ-001", "staging"]) == 0
    assert "Passed" in (repo / "docs" / "release" / "REQ-001" / "smoke-staging.md").read_text()
    with pytest.raises(state.StateError, match="set the version"):
        state.complete_stage(repo, "REQ-001", c)
    fill(repo, "release-notes", "Version: v1.4.0")
    assert release.version(repo, "REQ-001") == "v1.4.0"
    state.complete_stage(repo, "REQ-001", c)
    assert main(["approve", "REQ-001", "go"]) == 0
    assert "Release approved by" in (repo / "docs" / "release" / "REQ-001" / "release-notes.md").read_text()

    nxt = state.next_action(repo, c, "REQ-001")
    assert nxt["stage"] == "production" and nxt["action"] == "human_deploy"
    assert "never by an agent" in nxt["message"] and "--version v1.4.0" in nxt["message"]
    with pytest.raises(state.StateError, match="a person deploys to production"):
        state.complete_stage(repo, "REQ-001", c)
    assert main(["deployed", "REQ-001", "production", "--by", "ana", "--tag"]) == 0
    assert git(repo, "tag", "--list").split() == ["v1.4.0"]
    with pytest.raises(state.StateError, match="post-deployment check"):
        state.complete_stage(repo, "REQ-001", c)
    assert main(["smoke", "REQ-001", "production"]) == 0
    state.complete_stage(repo, "REQ-001", c)
    assert state.approve(repo, "REQ-001", c, "on-call") is None  # Monitoring is off by default: the item is done
    assert state.load(repo, "REQ-001")["status"] == "done"
    d = state.load(repo, "REQ-001")["deployments"]
    assert [(x["env"], x["by"]) for x in d] == [("staging", "agentic-sdlc"), ("production", "ana")]


def test_failed_smoke_points_to_rollback(repo: Path) -> None:
    c = config.load(repo)
    r = release.smoke(repo, "REQ-001", "staging", c)  # nothing deployed yet: .staging missing
    assert not r["passed"]
    assert "rm -f .staging" in (repo / r["report"]).read_text()


def test_production_can_never_be_made_agent_deployable(repo: Path) -> None:
    c = config.load(repo)
    c["environments"]["production"]["agent_ok"] = True
    with pytest.raises(config.ConfigError, match="always deployed by a person"):
        config.validate(c)
    c["environments"]["production"].pop("agent_ok")
    c["environments"]["perf"] = {"deploy": "x"}
    assert release.human_only(c, "perf") and release.human_only(c, "live") and not release.human_only(c, "staging")
    c["environments"]["perf"]["agent_ok"] = True
    assert not release.human_only(c, "perf")


def test_deploy_returns_while_the_started_server_keeps_running(tmp_path: Path) -> None:
    import time

    from agentic_sdlc import release

    # like scripts/staging.sh: a background server whose wrapper keeps the output open
    (tmp_path / "deploy.sh").write_text("#!/bin/sh\n( sleep 30 & echo started ) \necho deployed\n")
    (tmp_path / "deploy.sh").chmod(0o755)
    start = time.monotonic()
    r = release._run(tmp_path, "./deploy.sh", timeout=20)
    assert r["exit_code"] == 0 and "deployed" in r["output"] and time.monotonic() - start < 5


def test_ready_to_go_live_then_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from fastapi.testclient import TestClient

    from agentic_sdlc import config, human, release, state
    from agentic_sdlc.cli import main
    from agentic_sdlc.dashboard.server import create_app

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.json").write_text(json.dumps({"project": {"name": "d", "type": "new"}}))
    main(["init", "--answers", "a.json"])
    c = config.load(tmp_path)
    req = state.new_item(tmp_path, c, "Thing", "task")["id"]
    assert release.go_live(c, state.load(tmp_path, req), tmp_path) is None  # not before Release is approved
    with state.edit(tmp_path, req) as it:
        it["stages"]["release"]["status"] = state.APPROVED
    client = TestClient(create_app(tmp_path), headers=human.browser_headers())
    assert [g["req"] for g in client.get("/api/go-live").json()] == [req]
    assert client.get(f"/api/items/{req}").json()["go_live"]["state"] == "ready"
    r = client.post(f"/api/items/{req}/went-live", json={"version": "v0.1.1"}).json()
    assert r["go_live"]["state"] == "live" and r["go_live"]["deployed"]["version"] == "v0.1.1"
    assert client.get("/api/go-live").json() == []
    assert TestClient(create_app(tmp_path)).post(f"/api/items/{req}/went-live", json={}).status_code == 403
