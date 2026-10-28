import json
import subprocess
from pathlib import Path

import pytest

from agentic_sdlc import config, events, generate, state, validate
from agentic_sdlc.cli import main

FILLED_BUG = {"## Report\n": "## Report\nList returns 500 on an empty DB.\n",
              "## Steps to reproduce\n1.": "## Steps to reproduce\n1. GET /expenses",
              "- Expected:": "- Expected: 200 []", "- Actual:": "- Actual: 500",
              "- Who and how many are affected:": "- Who and how many are affected: new users",
              "- Verdict: bug / not a bug (it is a change or feature) / cannot reproduce": "- Verdict: bug",
              "- Test data and environment:": "- Test data and environment: empty DB",
              "  - Given\n  - When\n  - Then": "  - Given an empty store\n  - When GET /expenses\n  - Then 200 []"}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}}))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def write_bug(root: Path, req: str = "REQ-001", **extra: str) -> Path:
    text = generate.render("bug.md", root, req=req, title="X", links="")
    for a, b in {**FILLED_BUG, **extra}.items():
        text = text.replace(a, b)
    p = root / "specs" / f"{req}.md"
    p.write_text(text)
    return p


def test_lint_catches_what_a_template_leaves_behind(repo: Path) -> None:
    assert main(["new", "Crash", "--type", "bug"]) == 0
    r = validate.spec_lint(repo, "REQ-001", "bug", "bug.md")
    assert not r["ok"] and "empty section: Report (write it, or `n/a: <reason>`)" in r["errors"]
    assert "AC-1: Given / When / Then is empty" in r["errors"]
    write_bug(repo)
    assert validate.spec_lint(repo, "REQ-001", "bug", "bug.md")["ok"]
    write_bug(repo, **{"List returns 500 on an empty DB.": "n/a"})
    assert "n/a without a reason: Report" in validate.spec_lint(repo, "REQ-001", "bug", "bug.md")["errors"]
    p = write_bug(repo)
    p.write_text(p.read_text().replace("## Verification", "## Checks"))
    assert "missing section: verification" in validate.spec_lint(repo, "REQ-001", "bug", "bug.md")["errors"]


def test_lint_ac_priority_and_duplicates(repo: Path) -> None:
    assert main(["new", "Refunds"]) == 0
    p = repo / "specs" / "REQ-001.md"
    p.write_text(p.read_text().replace("- **AC-1** (must)\n  - Given\n  - When\n  - Then",
                                       "- **AC-1** Full refund\n  - Given a paid order\n  - When refunded\n  - Then money back\n"
                                       "- **AC-1** (should) Again\n  - Given x\n  - When y\n  - Then z"))
    errors = validate.spec_lint(repo, "REQ-001", "feature", "spec.md")["errors"]
    assert "duplicate AC-1" in errors and "AC-1: set a priority (must, should or could)" in errors


def test_complete_refuses_an_unfinished_spec(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["new", "Crash", "--type", "bug"]) == 0
    state.start_stage(repo, "REQ-001")
    assert main(["complete", "REQ-001"]) == 1
    assert "the spec is not complete" in capsys.readouterr().err
    write_bug(repo)
    assert main(["lint", "REQ-001"]) == 0
    assert main(["complete", "REQ-001"]) == 0  # bug triage has no gate: moves on to Development
    assert state.load(repo, "REQ-001")["current_stage"] == "development"


def test_plan_must_cover_acs_and_design_ids_and_avoid_file_clashes(repo: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["new", "Refunds"]) == 0
    spec = repo / "specs" / "REQ-001.md"
    spec.write_text("### Acceptance criteria\n- **AC-1** (must) a\n- **AC-2** (should) b\n- **AC-3** (must) c\n")
    sec = repo / "docs" / "design" / "REQ-001" / "security.md"
    sec.parent.mkdir(parents=True)
    sec.write_text("## Verdict\nOK with mitigations\n- SEC-1: validate amounts\n- SEC-2:\n")  # SEC-2 is an empty line
    (repo / "docs" / "plan" / "REQ-001").mkdir(parents=True)
    (repo / "docs" / "plan" / "REQ-001" / "release-plan.md").write_text("- REL-1: feature flag refunds_v2\n")
    tasks = [{"id": "T1", "title": "a", "acceptance": ["AC-1"], "files": ["app/refunds/"]},
             {"id": "T2", "title": "b", "acceptance": ["AC-3", "AC-9"], "files": ["app/refunds/service.py"]}]
    r = validate.plan_check(repo, "REQ-001", tasks)
    assert r["missing_must"] == [] and r["missing_ids"] == ["REL-1 (docs/plan/REQ-001/release-plan.md)",
                                                            "SEC-1 (docs/design/REQ-001/security.md)"]
    assert "T1 and T2 both touch app/refunds/" in r["overlaps"][0] and r["unknown_refs"] == ["AC-9"]
    tasks[0]["acceptance"] += ["SEC-1", "REL-1"]
    tasks[1]["deps"] = ["T1"]  # no longer in the same wave
    assert validate.plan_check(repo, "REQ-001", tasks)["ok"]
    (repo / ".sdlc" / "plans" / "REQ-001.json").write_text(json.dumps({"tasks": tasks[:1]}))
    capsys.readouterr()
    assert main(["plan", "REQ-001"]) == 1  # loaded, but AC-3 has no task
    assert json.loads(capsys.readouterr().out)["coverage"]["missing_must"] == ["AC-3"]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.email=a@b", "-c", "user.name=a", *args],
                   cwd=root, check=True, capture_output=True)


def test_versions_from_tags_type_and_breaking_changes(repo: Path) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    assert validate.next_version(repo, "REQ-001", "feature")["version"] == "v0.1.0"  # first release
    git(repo, "tag", "v1.2.3")
    assert validate.next_version(repo, "REQ-001", "feature") == {
        "previous": "v1.2.3", "bump": "minor", "reason": "a feature", "version": "v1.3.0"}
    assert validate.next_version(repo, "REQ-001", "bug")["version"] == "v1.2.4"
    notes = repo / "docs" / "release" / "REQ-001" / "release-notes.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("Version: vX.Y.Z\n\n## Breaking changes and actions needed\n- `total` is now a map\n\n## Known issues\n")
    assert validate.next_version(repo, "REQ-001", "change")["version"] == "v2.0.0"
    other = repo / "docs" / "release" / "REQ-002" / "release-notes.md"
    other.parent.mkdir(parents=True)
    other.write_text("Version: v1.4.0\n")  # claimed by another item, not tagged yet
    assert validate.next_version(repo, "REQ-003", "bug")["version"] == "v1.4.1"


def test_release_notes_must_carry_the_computed_version(repo: Path, capsys: pytest.CaptureFixture) -> None:
    c = config.load(repo)
    c["stages"].update({s: {**c["stages"][s], "enabled": False} for s in ("design", "planning", "qa")})
    config.write(repo, c)
    c = config.load(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    git(repo, "tag", "v1.0.0")
    assert main(["new", "Crash", "--type", "bug"]) == 0
    write_bug(repo)
    state.start_stage(repo, "REQ-001")
    state.complete_stage(repo, "REQ-001", c)
    state.start_stage(repo, "REQ-001")
    state.update_task(repo, "REQ-001", c, "T1", "start")
    state.update_task(repo, "REQ-001", c, "T1", "done")
    state.record_checks(repo, "REQ-001", {"ts": events.now(), "head": "", "passed": True, "results": []})
    state.complete_stage(repo, "REQ-001", c)
    state.approve(repo, "REQ-001", c, "peer")
    assert main(["start", "REQ-001"]) == 0  # release: the pack is created
    notes = repo / "docs" / "release" / "REQ-001" / "release-notes.md"
    notes.write_text(notes.read_text().replace("Version: vX.Y.Z", "Version: v3.0.0"))
    assert main(["complete", "REQ-001"]) == 1
    assert "say v3.0.0 but the version is v1.0.1" in capsys.readouterr().err
    assert main(["version", "REQ-001", "--write"]) == 0 and notes.read_text().startswith("Version: v1.0.1")
    assert main(["version", "REQ-001", "--set", "v1.1.0", "--write"]) == 0
    assert state.load(repo, "REQ-001")["version_set"] == "v1.1.0" and notes.read_text().startswith("Version: v1.1.0")
