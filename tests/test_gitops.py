import json
import subprocess
from pathlib import Path

import pytest

from agentic_sdlc import config, gitops, state
from agentic_sdlc.cli import main


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    (tmp_path / "app.py").write_text("print('hi')\n")
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}}))
    main(["init", "--answers", "answers.json"])
    (tmp_path / "answers.json").unlink()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_prepare_commits_sdlc_files_on_work_item_branch(repo: Path) -> None:
    c = config.load(repo)
    req = state.new_item(repo, c, "Refunds")["id"]
    (repo / "specs" / f"{req}.md").write_text("# spec\n")
    (repo / "docs" / "adr" / f"ADR-{req}.md").write_text("# adr\n")
    (repo / ".sdlc" / "plans" / f"{req}.json").write_text('{"tasks": []}')

    r = gitops.prepare(repo, req)
    assert r["branch"] == "sdlc/REQ-001" and r["created"] and r["from"] == "main"
    assert set(r["committed"]) >= {"specs/REQ-001.md", "docs/adr/ADR-REQ-001.md", ".sdlc/plans/REQ-001.json"}
    assert not any(p.startswith(".sdlc/state") for p in r["committed"])
    assert git(repo, "status", "--porcelain") == ""  # runtime state is ignored, tree is clean
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "sdlc/REQ-001"
    assert git(repo, "log", "main", "--oneline").count("\n") == 1  # main untouched

    # idempotent: second call on the branch with nothing new does nothing
    again = gitops.prepare(repo, req)
    assert again["committed"] == [] and not again["created"]


def test_prepare_refuses_foreign_changes(repo: Path) -> None:
    req = state.new_item(repo, config.load(repo), "x")["id"]
    (repo / "app.py").write_text("print('changed')\n")
    with pytest.raises(gitops.GitError, match="app.py"):
        gitops.prepare(repo, req)
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"  # nothing switched


def test_prepare_untracks_previously_committed_state(repo: Path) -> None:
    req = state.new_item(repo, config.load(repo), "x")["id"]
    (repo / ".sdlc" / ".gitignore").write_text("events.jsonl\n")  # old-style ignore file
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "old setup committed state")
    assert git(repo, "ls-files", ".sdlc/state")
    gitops.prepare(repo, req)
    assert git(repo, "ls-files", ".sdlc/state") == ""
    assert git(repo, "status", "--porcelain") == ""


def test_prepare_cli_reports_errors(repo: Path, capsys: pytest.CaptureFixture) -> None:
    req = state.new_item(repo, config.load(repo), "x")["id"]
    (repo / "notes.txt").write_text("mine")
    assert main(["prepare", req]) == 1
    assert "notes.txt" in capsys.readouterr().err
