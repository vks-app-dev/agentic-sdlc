import json
import subprocess
from pathlib import Path

import pytest
import yaml

from agentic_sdlc import checks, config, events, state
from agentic_sdlc.cli import main


def git(root: Path, *args: str) -> None:
    # Throwaway repo with fake secrets on purpose: skip the machine's global git hooks.
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    answers = {"project": {"name": "demo", "type": "new"},
               "stack": {"commands": {"test": "python -c 'print(\"3 passed\")'", "lint": "true"}},
               "stages": {"design": {"enabled": False}, "planning": {"enabled": False}}}
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "init")
    assert main(["new", "Refunds"]) == 0
    c = config.load(tmp_path)
    state.start_stage(tmp_path, "REQ-001")
    state.complete_stage(tmp_path, "REQ-001", c)
    state.approve(tmp_path, "REQ-001", c, "lead")
    git(tmp_path, "checkout", "-q", "-b", "sdlc/REQ-001")
    state.start_stage(tmp_path, "REQ-001")  # no plan: one task T1
    return tmp_path


def merge_t1(root: Path) -> None:
    c = config.load(root)
    state.update_task(root, "REQ-001", c, "T1", "start")
    state.update_task(root, "REQ-001", c, "T1", "done")


def test_check_runs_configured_commands_and_scans_secrets(repo: Path, capsys: pytest.CaptureFixture) -> None:
    fake_key = "sk-" + "ant-api03-abcdefghijklmnopqrstuvwx"  # built at runtime so secret scanners don't flag this repo
    (repo / "app.py").write_text(f'API_KEY = "{fake_key}"\nPASSWORD = "changeme-please-123"\n'
                                 'DB_PASSWORD = "q8Zr2LmX0pTa7Kd3"\nNOTE = "my-private-medical-note"\n')
    (repo / "tests").mkdir()
    (repo / "tests" / "test_log.py").write_text('SECRET = "q8Zr2LmX0pTa7Kd3Vb"\nKEY = "AKIAABCDEFGHIJKLMNOP"\n')
    git(repo, "add", "app.py", "tests")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "REQ-001/T1: code")
    merge_t1(repo)
    assert main(["check", "REQ-001"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["results"] == {"lint": "passed", "test": "passed", "commit messages (built-in)": "passed",
                              "secret scan (built-in)": "failed"}
    assert "typecheck" in out["not_configured"]
    report = (repo / "docs" / "dev" / "REQ-001" / "checks.md").read_text()
    assert "FAILED" in report and "app.py:1 Anthropic key" in report and "app.py:3 hard-coded credential" in report
    assert "app.py:2" not in report and "app.py:4" not in report  # placeholder and word-like values ignored
    assert "tests/test_log.py:2 AWS access key" in report and "tests/test_log.py:1" not in report
    with pytest.raises(state.StateError, match="secret scan"):
        state.complete_stage(repo, "REQ-001", config.load(repo))
    (repo / "app.py").write_text('import os\nAPI_KEY = os.environ["API_KEY"]\n')
    (repo / "tests" / "test_log.py").write_text('SECRET = "q8Zr2LmX0pTa7Kd3Vb"\n')
    assert main(["check", "REQ-001"]) == 0
    assert state.complete_stage(repo, "REQ-001", config.load(repo)) == state.AWAITING


def test_checks_must_be_newer_than_the_last_merge(repo: Path) -> None:
    state.record_checks(repo, "REQ-001", {"ts": "2000-01-01T00:00:00+00:00", "head": "", "passed": True, "results": []})
    merge_t1(repo)
    with pytest.raises(state.StateError, match="merged after"):
        state.complete_stage(repo, "REQ-001", config.load(repo))


def test_checks_can_be_turned_off(repo: Path) -> None:
    raw = yaml.safe_load((repo / "sdlc.config.yaml").read_text())
    raw["stages"]["development"]["require_checks"] = False
    (repo / "sdlc.config.yaml").write_text(yaml.safe_dump(raw))
    merge_t1(repo)
    assert state.complete_stage(repo, "REQ-001", config.load(repo)) == state.AWAITING


def test_failing_or_missing_tool_fails(repo: Path) -> None:
    c = config.load(repo)
    c["stack"]["commands"] = {"test": "exit 3", "sast": "no-such-scanner-xyz ."}
    r = {x["name"]: x["status"] for x in checks.run(repo, "REQ-001", c)["results"]}
    assert r == {"test": "failed", "sast": "missing tool", "commit messages (built-in)": "passed",
                 "secret scan (built-in)": "passed"}


def test_fix_tasks_and_handover(repo: Path) -> None:
    merge_t1(repo)
    assert main(["fix", "REQ-001", "Restore 404 for unknown ids", "--covers", "AC-3", "--after", "T1"]) == 0
    tasks = state.load(repo, "REQ-001")["tasks"]
    assert [(t["id"], t["area"], t["deps"], t["acceptance"]) for t in tasks][1] == ("T2", "fix", ["T1"], ["AC-3"])
    assert state.next_action(repo, config.load(repo), "REQ-001")["ready_tasks"][0]["id"] == "T2"
    with pytest.raises(state.StateError, match="Unknown task"):
        state.add_task(repo, "REQ-001", "x", [], ["T9"])
    (repo / "migrations").mkdir()
    (repo / "migrations" / "001_refunds.sql").write_text("create table refunds (id int);\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "REQ-001/T2: migration")
    state.record_checks(repo, "REQ-001", {"ts": events.now(), "head": "abc1234", "passed": True, "results": []})
    assert main(["handover", "REQ-001"]) == 0
    p = repo / "docs" / "dev" / "REQ-001" / "handover.md"
    text = p.read_text()
    assert "Branch: `sdlc/REQ-001`" in text and "Checks: passed at `abc1234`" in text
    assert "Migrations: `migrations/001_refunds.sql`" in text and "| T2 | Restore 404 for unknown ids | fix | - |" in text
    p.write_text(text.replace("## Known limitations\n", "## Known limitations\nNo pagination yet.\n"))
    assert main(["handover", "REQ-001"]) == 0  # regenerating keeps the verifier's sections
    assert "No pagination yet." in p.read_text()


def test_commit_messages_must_name_the_work_item(repo: Path) -> None:
    (repo / "x.py").write_text("x = 1\n")
    git(repo, "add", "x.py")
    git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "quick fix")
    r = {x["name"]: x for x in checks.run(repo, "REQ-001", config.load(repo))["results"]}
    assert r["commit messages (built-in)"]["status"] == "failed" and "quick fix" in r["commit messages (built-in)"]["output"]
