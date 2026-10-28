import json
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml

from agentic_sdlc import checks, config, events, human, release, state, testenv, trains
from agentic_sdlc.cli import main


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    answers = {"project": {"name": "shop", "type": "new"},
               "stages": {s: {"enabled": False} for s in ("design", "planning", "qa")}}
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


# ---- coverage ----

@pytest.mark.parametrize("rel,text,pct", [
    ("coverage.json", json.dumps({"totals": {"percent_covered": 91.25}}), 91.25),
    ("coverage/coverage-summary.json", json.dumps({"total": {"lines": {"pct": 77.5}}}), 77.5),
    ("coverage.xml", '<coverage line-rate="0.8312">', 83.12),
    ("lcov.info", "SF:a\nLF:10\nLH:9\nend_of_record\nSF:b\nLF:10\nLH:5\n", 70.0),
])
def test_coverage_report_formats(tmp_path: Path, rel: str, text: str, pct: float) -> None:
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text(text)
    assert checks.coverage_percent(tmp_path) == pct


def test_coverage_from_printed_total_and_minimum(repo: Path) -> None:
    assert checks.coverage_percent(repo, "Name  Stmts\nTOTAL   120   18   85%\n") == 85.0
    c = config.load(repo)
    c["stack"]["commands"] = {"coverage": "echo 'TOTAL 100 30 70%'"}
    c["stages"]["development"]["coverage_min"] = 80
    r = {x["name"]: x for x in checks.run(repo, "REQ-001", c)["results"]}
    assert r["coverage"]["status"] == "failed" and "70.0% is below the minimum 80%" in r["coverage"]["output"]
    c["stages"]["development"]["coverage_min"] = 60
    report = checks.run(repo, "REQ-001", c)
    assert {x["name"]: x["status"] for x in report["results"]}["coverage"] == "passed" and report["coverage"] == 70.0
    c["stack"]["commands"] = {"coverage": "true"}
    assert {x["name"]: x["status"] for x in checks.run(repo, "REQ-001", c)["results"]}["coverage"] == "failed"


# ---- test environments ----

SERVER = """import http.server, os, sys
port = int(sys.argv[sys.argv.index('--port') + 1])
open(os.path.join(os.environ['SDLC_ENV_DATA_DIR'], 'started'), 'w').write(os.environ.get('DB', ''))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(os.environ['DB'].encode())
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', port), H).serve_forever()
"""


def test_env_up_and_down_isolates_each_copy(repo: Path, unused_port: int = 18431) -> None:
    (repo / "server.py").write_text(SERVER)
    c = config.load(repo)
    c["stack"]["commands"] = {"run": f"{shlex.quote(sys.executable)} server.py --port 8000"}
    c["test_env"] = {"env": {"DB": "{data}/app.db"}, "url": "http://127.0.0.1:{port}/"}
    config.write(repo, c)
    c = config.load(repo)
    a = testenv.up(repo, c, "qa-a", unused_port)
    b = testenv.up(repo, c, "qa-b", unused_port + 1)
    body_a = urllib.request.urlopen(a["url"], timeout=3).read().decode()
    body_b = urllib.request.urlopen(b["url"], timeout=3).read().decode()
    assert body_a.endswith("/app.db") and body_b.endswith("/app.db") and body_a != body_b  # own data each
    assert [e["name"] for e in testenv.listing(repo)] == ["qa-a", "qa-b"]
    with pytest.raises(testenv.EnvError, match="already up"):
        testenv.up(repo, c, "qa-a", unused_port + 2)
    for name in ("qa-a", "qa-b"):
        testenv.down(repo, c, name)
    assert testenv.listing(repo) == [] and not Path(a["data"]).exists()
    with pytest.raises(OSError):
        urllib.request.urlopen(a["url"], timeout=1)


def test_env_reports_an_app_that_never_answers(repo: Path) -> None:
    c = config.load(repo)
    c["stack"]["commands"] = {"run": "echo boom && exit 1"}
    with pytest.raises(testenv.EnvError, match="(?s)did not answer.*boom"):
        testenv.up(repo, c, "qa-x", 18499, timeout=5)
    assert testenv.listing(repo) == []


# ---- release trains ----

def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.email=a@b", "-c", "user.name=a", *args],
                   cwd=root, check=True, capture_output=True)


def to_release(root: Path, title: str, wtype: str) -> str:
    c = config.load(root)
    assert main(["new", title, "--type", wtype]) == 0
    req = state.list_ids(root)[-1]
    while state.load(root, req)["current_stage"] != "development":
        state.start_stage(root, req)
        if state.complete_stage(root, req, c) == state.AWAITING:
            state.approve(root, req, c, "lead")
    state.start_stage(root, req)
    state.update_task(root, req, c, "T1", "start")
    state.update_task(root, req, c, "T1", "done")
    state.record_checks(root, req, {"ts": events.now(), "head": "", "passed": True, "results": []})
    state.complete_stage(root, req, c)
    state.approve(root, req, c, "peer")
    assert main(["start", req]) == 0  # Release: the pack
    for n in release.VERDICT_FILES:
        p = release.pack(root, req) / f"{n}.md"
        p.write_text("Verdict: PASS\n" + p.read_text().split("\n", 1)[1])
    return req


def test_release_train(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    git(repo, "tag", "v1.4.2")
    bug = to_release(repo, "Totals off by a cent", "bug")
    feat = to_release(repo, "Monthly statements", "feature")
    assert main(["train", "new", "2026.11", bug, feat]) == 0
    st = trains.status(repo, "2026.11")
    assert st["version"] == "v1.5.0"  # the feature's minor bump wins over the bug's patch
    assert state.load(repo, bug)["version_set"] == "v1.5.0"
    for req in (bug, feat):
        notes = release.pack(repo, req) / "release-notes.md"
        notes.write_text(notes.read_text().replace("## What's new\n-", f"## What's new\n- {req} change"))
        assert main(["version", req, "--write"]) == 0 and release.version(repo, req) == "v1.5.0"
        state.complete_stage(repo, req, config.load(repo))
    assert main(["approve", bug]) == 1  # an item in a train is approved through the train
    assert "approve the train" in capsys.readouterr().err
    monkeypatch.setattr(human, "interactive", lambda: False)
    assert main(["train", "approve", "2026.11"]) == 3  # an agent: filed for a person
    assert [p["action"] for p in human.pending(repo)] == ["train-approve"]
    monkeypatch.setattr(human, "interactive", lambda: True)
    assert main(["confirm", human.pending(repo)[0]["id"], "--by", "ana"]) == 0
    for req in (bug, feat):
        assert state.load(repo, req)["current_stage"] == "production"
    text = (repo / "docs" / "release" / "trains" / "2026.11.md").read_text()
    assert text.startswith("# Release v1.5.0 (2026.11)") and f"{bug} change" in text and "Approved by ana" in text
    assert main(["train", "deployed", "2026.11", "production", "--tag", "--by", "ana"]) == 0
    tags = subprocess.run(["git", "tag", "--list"], cwd=repo, capture_output=True, text=True).stdout.split()
    assert "v1.5.0" in tags and trains.load(repo, "2026.11")["status"] == "released"
    assert all(release.last(state.load(repo, r), "deployments", "production")["version"] == "v1.5.0" for r in (bug, feat))
    with pytest.raises(trains.TrainError, match="already in train"):
        trains.create(repo, "other", [bug])
