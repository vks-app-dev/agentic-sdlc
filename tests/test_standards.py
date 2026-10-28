import json
from pathlib import Path

import pytest
import yaml

from agentic_sdlc import config, standards, state
from agentic_sdlc.cli import main


def init(root: Path, **answers) -> None:
    (root / "a.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}, **answers}))
    assert main(["init", "--answers", "a.json"]) == 0


def test_defaults_are_written_and_listed_for_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    init(tmp_path)
    for name in standards.BUILTIN:
        assert (tmp_path / "docs" / "standards" / f"{name}.md").exists()
    cfg = config.load(tmp_path)
    rows = {r["name"]: r for r in standards.resolve(tmp_path, cfg)}
    assert rows["coding"]["source"] == "default" and "dev-coder" in rows["coding"]["agents"]
    assert "docs/standards/coding.md" in (tmp_path / "CLAUDE.md").read_text()
    # the agents of a stage are told which standards to read
    state.new_item(tmp_path, cfg, "Refunds", "task")
    steps = state.next_action(tmp_path, cfg, "REQ-001")["steps"]
    coder = next(s for s in steps if s["agent"] == "dev-coder")
    assert "docs/standards/coding.md" in coder["standards"] and "docs/standards/git.md" in coder["standards"]
    # an edited default is reported as edited, not as the default
    (tmp_path / "docs" / "standards" / "testing.md").write_text("# Our testing rules\n")
    assert {r["name"]: r for r in standards.resolve(tmp_path, cfg)}["testing"]["source"] == "edited"


def test_team_files_replace_the_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CONTRIBUTING.md").write_text("# How we code\n")
    (tmp_path / ".claude" / "skills" / "payments").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "payments" / "SKILL.md").write_text("---\nname: payments\n---\n")
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "spec.md").write_text("# {req}: {title}\nOur spec template\n")
    found = {f["path"]: f for f in standards.detect(tmp_path)}
    assert found["CONTRIBUTING.md"]["suggested_for"] == "git"
    assert found[".claude/skills/payments/SKILL.md"]["kind"] == "skill"

    init(tmp_path, templates_from="mine", standards={
        "coding": "CONTRIBUTING.md",
        "payments": {"path": ".claude/skills/payments/SKILL.md", "agents": ["dev-coder"]}})
    assert not (tmp_path / "docs" / "standards" / "coding.md").exists()  # theirs is used, no default copy
    assert "Our spec template" in (tmp_path / "specs" / "TEMPLATE.md").read_text()
    cfg = config.load(tmp_path)
    rows = {r["name"]: r for r in standards.resolve(tmp_path, cfg)}
    assert rows["coding"]["source"] == "yours" and rows["payments"]["kind"] == "skill"
    assert standards.for_agent(tmp_path, cfg, "dev-coder")[:1] == ["CONTRIBUTING.md"]
    assert ".claude/skills/payments/SKILL.md" in standards.for_agent(tmp_path, cfg, "dev-coder")
    assert ".claude/skills/payments/SKILL.md" not in standards.for_agent(tmp_path, cfg, "qa-uat-tester")


def test_use_and_reset_from_the_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    init(tmp_path)
    (tmp_path / "docs" / "api-style.md").write_text("# Our API rules\n")
    assert main(["standards", "use", "api", "docs/api-style.md"]) == 0
    assert yaml.safe_load((tmp_path / "sdlc.config.yaml").read_text())["standards"] == {"api": "docs/api-style.md"}
    assert "docs/api-style.md" in (tmp_path / "CLAUDE.md").read_text()
    assert main(["standards", "use", "api", "nope.md"]) == 1
    assert main(["standards", "reset", "api"]) == 0
    assert "standards" not in yaml.safe_load((tmp_path / "sdlc.config.yaml").read_text())


def test_library_lists_standards_and_templates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from agentic_sdlc.dashboard.server import create_app

    monkeypatch.chdir(tmp_path)
    init(tmp_path)
    (tmp_path / "docs" / "STYLE_GUIDE.md").write_text("# Style\n")
    lib = TestClient(create_app(tmp_path)).get("/api/library").json()
    assert len(lib["standards"]) == len(standards.BUILTIN)
    spec = next(t for t in lib["templates"] if t["name"] == "spec.md")
    assert spec["group"] == "Specs" and spec["source"] == "default" and "req-analyst" in spec["agents"]
    assert {t["group"] for t in lib["templates"]} >= {"Specs", "Design", "Planning", "QA", "Release"}
    assert [f["path"] for f in lib["found"]] == ["docs/STYLE_GUIDE.md"]  # a candidate the team could adopt


def test_people_replace_reset_and_add_from_the_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from agentic_sdlc import human
    from agentic_sdlc.dashboard.server import create_app

    monkeypatch.chdir(tmp_path)
    init(tmp_path)
    app = create_app(tmp_path)
    client = TestClient(app, headers=human.browser_headers())

    r = client.post("/api/library/standard/coding", json={"content": "# Our coding rules\n", "filename": "ours.md"})
    assert r.status_code == 200 and r.json()["entry"]["source"] == "edited"
    assert (tmp_path / "docs/standards/coding.md").read_text() == "# Our coding rules\n"
    assert r.json()["previous"].startswith(".sdlc/library-history/")  # the old version is kept
    down = client.get("/api/library/standard/download", params={"name": "coding"})
    assert down.text == "# Our coding rules\n" and "attachment" in down.headers["content-disposition"]

    t = client.post("/api/library/template/design/api.md", json={"content": "# API {req}\n", "filename": "api.md"})
    assert t.json()["entry"]["source"] == "edited"
    assert client.post("/api/library/template/design/api.md/reset").json()["entry"]["source"] == "default"
    assert client.post("/api/library/standard/coding/reset").json()["entry"]["source"] == "default"

    assert client.post("/api/library/standard/coding", json={"content": "x", "filename": "a.exe"}).status_code == 422
    new = client.post("/api/library/standard", json={"name": "payments", "title": "Payments rules", "skill": True,
                                                     "agents": ["dev-coder"], "content": "Never store card numbers."})
    assert new.status_code == 200 and new.json()["path"] == ".claude/skills/payments/SKILL.md"
    assert (tmp_path / ".claude/skills/payments/SKILL.md").read_text().startswith("---\nname: payments")
    assert ".claude/skills/payments/SKILL.md" in standards.for_agent(tmp_path, config.load(tmp_path), "dev-coder")
    assert "Payments rules" in (tmp_path / "CLAUDE.md").read_text()
    assert client.delete("/api/library/standard/payments").status_code == 200
    # a new standard needs its agents chosen (or "all"); one that looks like an existing standard is pointed at it
    assert client.post("/api/library/standard", json={"name": "glossary", "content": "# Terms\n"}).status_code == 422
    assert client.post("/api/library/standard", json={"name": "glossary", "agents": "all", "content": "# Terms\n"}).status_code == 200
    assert "docs/standards/glossary.md" in standards.for_agent(tmp_path, config.load(tmp_path), "qa-uat-tester")
    client.delete("/api/library/standard/glossary")
    copy = (tmp_path / "docs/standards/testing.md").read_text()
    assert client.post("/api/library/similar", json={"content": copy}).json()["match"]["name"] == "testing"
    assert client.post("/api/library/similar", params={"title": "Coding standards"},
                       json={"content": "# x\n"}).json()["match"]["name"] == "coding"
    assert client.post("/api/library/similar", params={"title": "Payments rules"}, json={"content": "# x\n"}).json()["match"] is None
    assert "standards" not in yaml.safe_load((tmp_path / "sdlc.config.yaml").read_text())
    # only a person in the dashboard can change what agents follow
    assert TestClient(app).post("/api/library/standard/coding", json={"content": "y"}).status_code == 403
