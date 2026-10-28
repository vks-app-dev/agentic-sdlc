import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentic_sdlc import config, events, state
from agentic_sdlc import human
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.server import create_app, live_agents


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}}))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


@pytest.fixture
def client(repo: Path) -> TestClient:
    return TestClient(create_app(repo), headers=human.browser_headers())


def test_project_and_items(repo: Path, client: TestClient) -> None:
    state.new_item(repo, config.load(repo), "Refunds")
    assert client.get("/api/project").json()["project"]["name"] == "demo"
    items = client.get("/api/items").json()
    assert [i["id"] for i in items] == ["REQ-001"]
    detail = client.get("/api/items/REQ-001").json()
    assert detail["next"]["action"] == "run_stage"
    assert client.get("/api/items/REQ-999").status_code == 409


def test_gate_decisions_from_ui(repo: Path, client: TestClient) -> None:
    c = config.load(repo)
    state.new_item(repo, c, "Refunds")
    assert client.post("/api/items/REQ-001/approve", json={}).status_code == 409  # not at a gate yet
    state.start_stage(repo, "REQ-001")
    state.complete_stage(repo, "REQ-001", c)
    assert client.post("/api/items/REQ-001/reject", json={"comment": " "}).status_code == 422
    assert client.post("/api/items/REQ-001/reject", json={"by": "ana", "comment": "add limits"}).json()["ok"]
    state.complete_stage(repo, "REQ-001", c)
    r = client.post("/api/items/REQ-001/approve", json={"by": "ana", "comment": "ok"}).json()
    assert r["next_stage"] == "design"
    m = client.get("/api/metrics").json()
    assert m["gates"] == {"approved": 1, "rejected": 1, "rework_rate": 0.5, "avg_wait_min": 0.0}


def test_file_access_is_confined_to_repo(repo: Path, client: TestClient) -> None:
    assert "Agentic SDLC" in client.get("/api/file", params={"path": "CLAUDE.md"}).json()["content"]
    assert client.get("/api/file", params={"path": "../../etc/passwd"}).status_code == 403
    assert client.get("/api/file", params={"path": "missing.md"}).status_code == 404


def test_live_agents_tracks_start_and_stop() -> None:
    evs = [
        {"ts": "t1", "event": "agent_start", "agent_id": "a1", "agent": "dev-coder", "req": "REQ-001", "task": "T1"},
        {"ts": "t2", "event": "agent_start", "agent_id": "a2", "agent": "dev-coder", "req": "REQ-001", "task": "T2"},
        {"ts": "t3", "event": "tool_use", "agent_id": "a1", "tool": "Bash", "detail": "pytest"},
        {"ts": "t4", "event": "agent_stop", "agent_id": "a2"},
    ]
    live = live_agents(evs)
    assert [a["task"] for a in live] == ["T1"] and live[0]["last"] == "Bash: pytest"


def test_stream_sends_new_events(repo: Path) -> None:
    import threading
    import time

    app = create_app(repo)

    def later() -> None:
        time.sleep(0.8)
        events.emit(repo, "custom", req="REQ-001", detail="hello")

    threading.Thread(target=later, daemon=True).start()
    with TestClient(app, headers=human.browser_headers()) as c, c.stream("GET", "/api/stream") as r:
        got = []
        for line in r.iter_lines():
            if line.startswith("data: {") and line != "data: {}":
                got.append(json.loads(line[6:]))
                break
    assert got[0]["event"] == "custom"


FAKE_CLAUDE = """#!/bin/sh
echo '{"type":"system","subtype":"init","model":"fake"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"Dispatching req-analyst"},{"type":"tool_use","name":"Agent","input":{"description":"REQ-001 requirements spec"}}]}}'
sleep "${FAKE_SLEEP:-0}"
echo '{"type":"result","duration_ms":1200,"total_cost_usd":0.05,"result":"Stopped at the requirements gate."}'
"""


def wait_for(fn, timeout: float = 5.0):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if v := fn():
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_run_agents_from_dashboard(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = repo / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_CLAUDE_BIN", str(fake))
    c = config.load(repo)
    state.new_item(repo, c, "Refunds")
    client = TestClient(create_app(repo), headers=human.browser_headers())

    run = client.post("/api/items/REQ-001/run").json()
    assert run["status"] == "running" and "/agentic-sdlc:sdlc-run REQ-001" in run["command"]
    assert "Bash(sdlc:*)" in run["command"]
    assert client.post("/api/items/REQ-001/run").status_code == 409  # one run per item
    done = wait_for(lambda: (r := client.get(f"/api/runs/{run['id']}/log").json())["run"]["status"] != "running" and r)
    assert done["run"]["status"] == "finished" and done["run"]["exit_code"] == 0
    assert "→ Agent: REQ-001 requirements spec" in done["text"] and "Stopped at the requirements gate." in done["text"]
    kinds = [e["event"] for e in events.read(repo)]
    assert "run_started" in kinds and wait_for(lambda: "run_finished" in [e["event"] for e in events.read(repo)])


def test_run_refused_at_gate_and_stoppable(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = repo / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_CLAUDE_BIN", str(fake))
    monkeypatch.setenv("FAKE_SLEEP", "30")
    c = config.load(repo)
    state.new_item(repo, c, "A")
    state.new_item(repo, c, "B")
    state.start_stage(repo, "REQ-001")
    state.complete_stage(repo, "REQ-001", c)
    client = TestClient(create_app(repo), headers=human.browser_headers())
    r = client.post("/api/items/REQ-001/run")
    assert r.status_code == 409 and "approval" in r.json()["detail"]
    run = client.post("/api/items/REQ-002/run").json()
    assert client.post(f"/api/runs/{run['id']}/stop").json()["status"] == "stopped"
    wait_for(lambda: client.get(f"/api/runs/{run['id']}/log").json()["run"]["exit_code"] is not None)


def test_home_classifies_a_request_and_starts_the_agents(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = repo / "fake-claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_CLAUDE_BIN", str(fake))
    c = config.load(repo)
    state.new_item(repo, c, "Record and list expenses")
    client = TestClient(create_app(repo), headers=human.browser_headers())

    home = client.get("/api/home").json()
    leads = {t["stage"]: t for t in home["team"]}
    assert leads["requirements"]["status"] == "ready" and leads["production"]["status"] == "human"
    req_agents = [a["name"] for a in leads["requirements"]["agents"]]
    assert req_agents[:3] == ["req-analyst", "req-nfr-analyst", "req-ambiguity-checker"]
    assert {"req-bug-triager", "req-change-impact-analyst"} <= set(req_agents)  # only for bugs / changes
    assert next(a for a in leads["requirements"]["agents"] if a["name"] == "req-bug-triager")["for"] == ["bug"]
    assert home["last_job"]["req"] == "REQ-001" and home["last_job"]["can_resume"]
    kinds = {w["name"]: [p["stage"] for p in w["plan"]] for w in home["work_types"]}
    assert kinds["bug"][:2] == ["requirements", "development"] and kinds["task"][0] == "development"

    def kind(text: str) -> dict:
        return client.post("/api/home/classify", json={"text": text}).json()

    assert kind("Please let users export expenses as CSV")["type"] == "feature"
    change = kind("Expenses can be edited after they are recorded")
    assert change["type"] == "change" and change["links"] == [{"rel": "updates", "req": "REQ-001"}]
    bug = kind("Listing expenses crashes with a 500 when the list is empty")
    assert bug["type"] == "bug" and bug["links"][0]["rel"] == "fixes"
    assert kind("Upgrade FastAPI and refactor the database module")["type"] == "task"
    assert [p["stage"] for p in bug["plan"]][:2] == ["requirements", "development"]

    started = client.post("/api/home/start", json={"text": "Expenses can be edited", "title": change["title"],
                                                   "type": "change", "links": change["links"]}).json()
    assert started["req"] == "REQ-002" and started["run"]["status"] == "running"
    spec = (repo / "specs" / "REQ-002.md").read_text()
    assert "## Request\n> Expenses can be edited" in spec and spec.startswith("# REQ-002")
    wait_for(lambda: client.get(f"/api/runs/{started['run']['id']}/log").json()["run"]["status"] != "running")
    # a person must confirm: a plain API call (no page token) is refused
    assert TestClient(create_app(repo)).post("/api/home/start", json={"text": "x", "title": "x", "type": "task"}).status_code == 403


def test_failed_run_says_why_and_login_mode_drops_the_key(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_sdlc.dashboard.runner import run_env

    fake = repo / "fake-claude"
    fake.write_text('#!/bin/sh\necho "{\\"type\\":\\"result\\",\\"is_error\\":true,\\"result\\":\\"Invalid API key · Fix external API key\\"}"\nexit 1\n')
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_CLAUDE_BIN", str(fake))
    state.new_item(repo, config.load(repo), "Refunds")
    client = TestClient(create_app(repo), headers=human.browser_headers())
    run = client.post("/api/items/REQ-001/run").json()
    done = wait_for(lambda: (r := client.get(f"/api/runs/{run['id']}/log").json())["run"]["status"] != "running" and r)
    assert done["run"]["status"] == "failed" and "SDLC_RUN_AUTH=login" in done["run"]["error"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bad")
    assert run_env("r1")["ANTHROPIC_API_KEY"] == "sk-bad"
    monkeypatch.setenv("SDLC_RUN_AUTH", "login")
    assert "ANTHROPIC_API_KEY" not in run_env("r1")


def test_dashboard_reuses_its_port(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    from agentic_sdlc import cli

    monkeypatch.setattr(cli, "_dashboard_at", lambda url: str(repo))
    assert main(["dashboard", "--no-open"]) == 0
    assert "already running: http://127.0.0.1:8765" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_dashboard_at", lambda url: "/elsewhere/other-project")
    assert main(["dashboard", "--no-open"]) == 1
    assert "Port 8765 already has the dashboard of /elsewhere/other-project" in capsys.readouterr().err


def test_item_flow_shows_planned_agents_and_what_ran(repo: Path) -> None:
    from agentic_sdlc import assistant

    c = config.load(repo)
    state.new_item(repo, c, "Upgrade FastAPI", "task")
    state.start_stage(repo, "REQ-001")
    it = state.load(repo, "REQ-001")
    after = it["stages"]["development"]["started"]
    evs = [{"ts": after, "event": "agent_start", "agent_id": "a1", "agent": "agentic-sdlc:dev-test-writer", "req": "REQ-001"},
           {"ts": after, "event": "agent_stop", "agent_id": "a1", "agent": "agentic-sdlc:dev-test-writer"},
           {"ts": after, "event": "agent_start", "agent_id": "a2", "agent": "agentic-sdlc:dev-coder", "req": "REQ-001"},
           {"ts": after, "event": "agent_start", "agent_id": "a3", "agent": "agentic-sdlc:dev-hotfixer", "req": "REQ-001"}]
    flow = assistant.item_flow(repo, c, it, evs)
    assert [f["stage"] for f in flow][0] == "development"  # a task skips requirements, design and planning
    dev = {a["name"]: a["status"] for a in flow[0]["agents"]}
    assert dev["dev-test-writer"] == "done" and dev["dev-coder"] == "working" and dev["dev-code-reviewer"] == "waiting"
    assert flow[0]["extra"] == [{"name": "dev-hotfixer", "status": "working", "runs": 1}]  # ran but not planned
    assert TestClient(create_app(repo)).get("/api/items/REQ-001").json()["flow"][0]["stage"] == "development"


def test_sign_in_help(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_sdlc.dashboard.runner import failure_reason

    fake = repo / "fake-claude"
    fake.write_text('#!/bin/sh\nif [ "$1 $2" = "auth status" ]; then echo \'{"loggedIn": true, "authMethod": "claude.ai", '
                    '"email": "a@b.c"}\'; fi\n')
    fake.chmod(0o755)
    monkeypatch.setenv("SDLC_CLAUDE_BIN", str(fake))
    client = TestClient(create_app(repo), headers=human.browser_headers())
    assert client.get("/api/claude/auth").json() == {"logged_in": True, "method": "claude.ai", "email": "a@b.c"}
    assert client.post("/api/claude/login").json()["started"]
    assert TestClient(create_app(repo)).post("/api/claude/login").status_code == 403  # people only
    log = repo / "run.log"
    log.write_text('{"type":"result","is_error":true,"result":"Failed to authenticate: OAuth session expired"}\n')
    assert "Sign in again" in failure_reason(log)


def test_live_view_shows_orchestrator_agents_and_handoffs(repo: Path) -> None:
    from agentic_sdlc import assistant

    c = config.load(repo)
    state.new_item(repo, c, "Upgrade", "task")
    state.start_stage(repo, "REQ-001")
    t = state.load(repo, "REQ-001")["stages"]["development"]["started"]
    evs = [{"ts": t, "event": "tool_use", "agent": "orchestrator", "tool": "Bash", "detail": "sdlc task REQ-001 T1 start", "req": "REQ-001"},
           {"ts": t, "event": "agent_start", "agent_id": "a1", "agent": "agentic-sdlc:dev-coder", "req": "REQ-001", "task": "T1"},
           {"ts": t, "event": "tool_use", "agent_id": "a1", "agent": "agentic-sdlc:dev-coder", "tool": "Edit", "detail": "app/main.py", "req": "REQ-001"}]
    v = assistant.live_view(repo, c, state.load(repo, "REQ-001"), evs, running=True)
    coder = next(a for a in v["agents"] if a["name"] == "dev-coder")
    assert v["stage"] == "development" and v["orchestrator"]["status"] == "working"
    assert coder["status"] == "working" and coder["runs"][0]["last"] == "Edit: app/main.py"
    assert [h["kind"] for h in v["handoffs"]] == ["dispatch", "orchestrator"]  # newest first
    assert TestClient(create_app(repo)).get("/api/items/REQ-001/live").json()["stage"] == "development"
