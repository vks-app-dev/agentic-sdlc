import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agentic_sdlc import __version__, checks, config, doctor, generate, portfolio, state
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.server import create_portfolio_app

ORG = {"version": 1, "name": "Acme", "min_version": "0.7.0",
       "defaults": {"stages": {"qa": {"gate": ["qa-lead"]}}, "integrations": {"hosts": ["claude"]}},
       "policy": {"required_stages": ["qa", "release"], "min_gates": {"release": ["release-manager"]},
                  "compliance": ["soc2"], "require": {"lint": True, "plan_check": True},
                  "required_commands": ["secrets"], "max_model": "sonnet", "pull_requests": True,
                  "human_only_envs": ["eu-prod"]},
       "templates": "templates", "projects": ["../repo"]}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    (tmp_path / "org").mkdir()
    (tmp_path / "org" / "org.yaml").write_text(yaml.safe_dump(ORG))
    answers = {"project": {"name": "shop", "type": "new", "compliance": ["gdpr"]},
               "stages": {"qa": {"enabled": False}, "planning": {"require_plan_check": False},
                          "release": {"gate": ["tech-lead"]}},
               "models": {"agents": {"design-architect": "opus", "rel-notes-writer": "haiku"}},
               "environments": {"eu-prod": {"deploy": "./ship.sh", "agent_ok": True}}}
    (root / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    raw = yaml.safe_load((root / "sdlc.config.yaml").read_text())
    raw["org"] = "../org"
    (root / "sdlc.config.yaml").write_text(yaml.safe_dump(raw))
    return root


def test_policy_is_enforced_on_the_effective_config(repo: Path) -> None:
    c = config.load(repo)
    assert c["stages"]["qa"]["enabled"] is True and c["stages"]["qa"]["gate"] == ["qa-lead"]  # default + policy
    assert c["stages"]["release"]["gate"] == ["tech-lead", "release-manager"]  # tightened, not replaced
    assert c["project"]["compliance"] == ["gdpr", "soc2"]
    assert c["stages"]["planning"]["require_plan_check"] is True and c["integrations"]["pull_requests"] is True
    assert config.agent_model(c, "design-architect") == "sonnet"  # max_model clamps overrides ...
    assert config.agent_model(c, "design-security-reviewer") == "sonnet"  # ... and defaults
    assert config.agent_model(c, "rel-notes-writer") == "haiku"
    assert c["environments"]["eu-prod"]["human_only"] and "agent_ok" not in c["environments"]["eu-prod"]
    notes = c["_org"]["notes"]
    assert "stages.qa turned back on (required by Acme)" in notes and any("max_model" in n for n in notes)
    config.write(repo, c)  # the org's effect is never saved into the project's file
    assert "_org" not in yaml.safe_load((repo / "sdlc.config.yaml").read_text())


def test_version_pins(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = yaml.safe_load((repo / "sdlc.config.yaml").read_text())
    raw["requires"] = ">=99.0"
    (repo / "sdlc.config.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(config.ConfigError, match=f"needs agentic-sdlc >=99.0; this machine has {__version__}"):
        config.load(repo)
    raw["requires"] = "0.7"
    (repo / "sdlc.config.yaml").write_text(yaml.safe_dump(raw))
    config.load(repo)
    org = yaml.safe_load((repo.parent / "org" / "org.yaml").read_text())
    org["min_version"] = "1.0.0"
    (repo.parent / "org" / "org.yaml").write_text(yaml.safe_dump(org))
    with pytest.raises(config.ConfigError, match="Company policy"):
        config.load(repo)


def test_org_templates_sit_between_package_and_project(repo: Path) -> None:
    (repo.parent / "org" / "templates").mkdir()
    (repo.parent / "org" / "templates" / "bug.md").write_text("# {req}: {title}\n\n## Acme incident ID\n")
    config.load(repo)
    (repo / "specs" / "TEMPLATE-bug.md").unlink()  # no project copy: the org's template is the default
    assert "Acme incident ID" in generate.render("bug.md", repo, req="REQ-1", title="x")
    assert "## Report" in generate.template("spec.md") or "Business context" in generate.template("spec.md")
    generate.ORG_TEMPLATES = None


def test_required_commands_fail_checks_and_doctor(repo: Path) -> None:
    c = config.load(repo)
    names = {r["name"]: r["status"] for r in checks.run(repo, "REQ-001", c)["results"]}
    assert names["secrets"] == "failed"
    rows = {r["check"]: r["status"] for r in doctor.run(repo)}
    assert rows["required check: secrets"] == "fail" and rows["company policy"] == "ok"
    assert rows["git"] == "fail" and doctor.failed(doctor.run(repo))


def test_invalid_org_file(repo: Path) -> None:
    (repo.parent / "org" / "org.yaml").write_text(yaml.safe_dump({"version": 1, "policy": {"max_model": "gpt"}}))
    with pytest.raises(config.ConfigError, match="max_model"):
        config.load(repo)


def test_portfolio_across_projects(repo: Path, capsys: pytest.CaptureFixture) -> None:
    c = config.load(repo)
    assert main(["new", "Refunds"]) == 0
    state.start_stage(repo, "REQ-001")
    state.complete_stage(repo, "REQ-001", c)
    missing = repo.parent / "gone"
    data = portfolio.collect([repo, missing])
    shop = data["projects"][0]
    assert shop["name"] == "shop" and shop["org"] == "Acme" and shop["waiting"][0]["stage"] == "requirements"
    assert "error" in data["projects"][1] and data["totals"]["gates_waiting"] == 1
    assert [Path(p).name for p in portfolio.paths_from([], repo)] == ["repo"]  # from org.yaml `projects:`
    capsys.readouterr()
    assert main(["portfolio", str(repo)]) == 0
    assert "Waiting for people" in capsys.readouterr().out
    api = TestClient(create_portfolio_app([repo]))
    assert api.get("/api/mode").json() == {"mode": "portfolio"}
    assert api.get("/api/portfolio").json()["totals"]["projects"] == 1


def test_version_flag(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip() == f"agentic-sdlc {__version__}"
    plugin = json.loads((Path(__file__).parents[1] / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == __version__  # the CLI and the plugin are released together


def test_upgrade_adds_what_is_new_and_keeps_what_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.json").write_text(json.dumps({"project": {"name": "old", "type": "new"}}))
    assert main(["init", "--answers", "a.json"]) == 0
    raw = yaml.safe_load((tmp_path / "sdlc.config.yaml").read_text())
    del raw["stages"]["planning"], raw["work_types"], raw["integrations"]["hosts"]  # an older version's config
    raw["stages"]["qa"]["enabled"] = False
    (tmp_path / "sdlc.config.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "docs" / "design" / "TEMPLATE" / "security.md").write_text("old copy\n")
    (tmp_path / "CLAUDE.md").unlink()
    from agentic_sdlc import upgrade
    r = upgrade.run(tmp_path)
    assert {"stages.planning", "work_types", "integrations.hosts"} <= set(r["config_added"])
    new = yaml.safe_load((tmp_path / "sdlc.config.yaml").read_text())
    assert new["stages"]["qa"]["enabled"] is False  # a value the repo set is kept
    assert (tmp_path / "CLAUDE.md").exists() and r["templates_differ"] == ["docs/design/TEMPLATE/security.md"]
    assert (tmp_path / "docs" / "design" / "TEMPLATE" / "security.md").read_text() == "old copy\n"
    upgrade.run(tmp_path, replace_templates=True)
    assert "SEC-1" in (tmp_path / "docs" / "design" / "TEMPLATE" / "security.md").read_text()
    assert upgrade.run(tmp_path)["config_added"] == []  # running it again changes nothing


def test_projects_set_up_before_the_rename_still_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.json").write_text(json.dumps({"project": {"name": "old", "type": "new"}}))
    assert main(["init", "--answers", "a.json"]) == 0
    md = tmp_path / "CLAUDE.md"
    text = md.read_text().replace("agentic-sdlc:start", "sdlc-pilot:start").replace("agentic-sdlc:end", "sdlc-pilot:end")
    md.write_text("# My notes\n\nKeep this.\n\n" + text.split("\n", 2)[2])  # an old marked section after own notes
    from agentic_sdlc import upgrade
    upgrade.run(tmp_path)
    new = md.read_text()
    assert "Keep this." in new and new.count("## Agentic SDLC") == 1 and "sdlc-pilot:start" not in new
