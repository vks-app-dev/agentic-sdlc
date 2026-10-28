import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parents[1] / "plugin" / "hooks" / "emit_event.py"


def fire(root: Path, payload: dict) -> None:
    payload.setdefault("cwd", str(root))
    payload.setdefault("session_id", "abcdef123456")
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                       capture_output=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert r.stderr == "", r.stderr


def lines(root: Path) -> list[dict]:
    p = root / ".sdlc" / "events.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_silent_outside_sdlc_repo(tmp_path: Path) -> None:
    fire(tmp_path, {"hook_event_name": "Stop"})
    assert not (tmp_path / ".sdlc").exists()


def test_subagent_tool_calls_are_tagged_with_req_and_task(tmp_path: Path) -> None:
    (tmp_path / "sdlc.config.yaml").write_text("version: 1\n")
    fire(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                    "tool_input": {"subagent_type": "agentic-sdlc:dev-coder", "description": "REQ-001/T2 API endpoint",
                                   "prompt": "..."}})
    fire(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                    "tool_input": {"subagent_type": "agentic-sdlc:dev-coder", "description": "REQ-001/T3 UI form"}})
    fire(tmp_path, {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "agentic-sdlc:dev-coder"})
    fire(tmp_path, {"hook_event_name": "SubagentStart", "agent_id": "a2", "agent_type": "dev-coder"})
    fire(tmp_path, {"hook_event_name": "PostToolUse", "agent_id": "a2", "agent_type": "agentic-sdlc:dev-coder",
                    "tool_name": "Bash", "tool_input": {"command": "pytest -q"}, "tool_response": {"exit_code": 1}})
    fire(tmp_path, {"hook_event_name": "PostToolUse", "agent_id": "a1", "agent_type": "agentic-sdlc:dev-coder",
                    "tool_name": "Write", "tool_input": {"file_path": "src/api.py"}})
    fire(tmp_path, {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": "agentic-sdlc:dev-coder",
                    "last_assistant_message": "T2 done, tests pass"})
    ev = lines(tmp_path)
    assert [e["event"] for e in ev] == ["agent_dispatch", "agent_dispatch", "agent_start", "agent_start",
                                        "tool_use", "tool_use", "agent_stop"]
    bash, write, stop = ev[4], ev[5], ev[6]
    assert (bash["req"], bash["task"], bash["detail"]) == ("REQ-001", "T3", "pytest -q → exit 1")
    assert (write["task"], write["detail"]) == ("T2", "src/api.py")
    assert stop["task"] == "T2" and stop["status"] == "finished"


def test_main_session_falls_back_to_active_item(tmp_path: Path) -> None:
    (tmp_path / "sdlc.config.yaml").write_text("version: 1\n")
    run = tmp_path / ".sdlc" / "run"
    run.mkdir(parents=True)
    (run / "active.json").write_text(json.dumps({"req": "REQ-004", "stage": "design"}))
    fire(tmp_path, {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                    "tool_input": {"file_path": "docs/adr/ADR-REQ-004.md"}})
    e = lines(tmp_path)[0]
    assert e["req"] == "REQ-004" and e["agent"] == "orchestrator" and e["source"] == "hook"


def test_garbage_input_never_fails(tmp_path: Path) -> None:
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True, capture_output=True)
    assert r.returncode == 0


GUARD = Path(__file__).parents[1] / "plugin" / "hooks" / "guard.py"


def guard(tool: str, inp: dict) -> str | None:
    r = subprocess.run([sys.executable, str(GUARD)], text=True, capture_output=True, timeout=10,
                       input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp}))
    assert r.returncode == 0
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_guard_blocks_self_approval_and_state_edits() -> None:
    assert guard("Bash", {"command": "sdlc approve REQ-001"}) == "deny"
    assert guard("Bash", {"command": "cd x && sdlc approve REQ-001 -m ok"}) == "deny"
    assert guard("Bash", {"command": "git push --force origin main"}) == "deny"
    assert guard("Write", {"file_path": "/repo/.sdlc/state/REQ-001.json"}) == "deny"
    assert guard("Bash", {"command": "sdlc retype REQ-004 change works as specified"}) == "deny"
    for cmd in ("sdlc confirm REQ-001-approve-a1b2c3", "sdlc reject REQ-001 redo", "sdlc skip REQ-001 -m qa later",
                "sdlc task REQ-001 T3 unblock", "curl -X POST http://127.0.0.1:8765/api/items/REQ-001/approve",
                "curl -s localhost:8765/api/pending/x/confirm -d {}"):
        assert guard("Bash", {"command": cmd}) == "deny", cmd
    for cmd in ("git push origin sdlc/REQ-001", "gh pr merge 12 --squash", "gh pr review 12 --approve",
                "gh api repos/o/r/pulls/12/merge -X PUT", "cd x && gh pr create --fill"):
        assert guard("Bash", {"command": cmd}) == "deny", cmd


def test_guard_allows_normal_work() -> None:
    assert guard("Bash", {"command": "sdlc next REQ-001"}) is None
    assert guard("Bash", {"command": "sdlc status"}) is None
    assert guard("Bash", {"command": "sdlc task REQ-001 T3 start"}) is None
    assert guard("Bash", {"command": "curl -fsS http://127.0.0.1:8000/health"}) is None
    assert guard("Bash", {"command": "sdlc size REQ-004 S"}) is None
    assert guard("Bash", {"command": "gh pr view 12 --json files"}) is None
    assert guard("Bash", {"command": "gh pr diff 12"}) is None
    assert guard("Bash", {"command": "sdlc pr REQ-001"}) is None
    assert guard("Bash", {"command": "git log --grep push"}) is None
    assert guard("Write", {"file_path": "/repo/.sdlc/plans/REQ-001.json"}) is None


def test_main_session_bash_tagged_from_command(tmp_path: Path) -> None:
    (tmp_path / "sdlc.config.yaml").write_text("version: 1\n")
    fire(tmp_path, {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "sdlc next REQ-007"}})
    assert lines(tmp_path)[0]["req"] == "REQ-007"


def test_guard_keeps_production_human_only(tmp_path: Path) -> None:
    (tmp_path / "sdlc.config.yaml").write_text("version: 1\n")
    (tmp_path / ".sdlc" / "run").mkdir(parents=True)
    (tmp_path / ".sdlc" / "run" / "guard.json").write_text(json.dumps(
        {"human_only_envs": ["production", "prod", "live", "eu-prod"], "commands": ["./scripts/ship.sh --target main"]}))

    def g(cmd: str) -> str | None:
        r = subprocess.run([sys.executable, str(GUARD)], text=True, capture_output=True, timeout=10, input=json.dumps(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path), "tool_input": {"command": cmd}}))
        return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] if r.stdout.strip() else None

    for cmd in ("sdlc deploy REQ-001 production", "sdlc deploy REQ-001 eu-prod", "sdlc smoke REQ-001 production",
                "sdlc deployed REQ-001 production --tag", "./scripts/ship.sh --target main",
                "vercel deploy --prod", "kubectl apply -f k8s/ -n production", "helm upgrade api ./chart -f values-prod.yaml",
                "fly deploy -a shop-prod", "npm run deploy:prod", "terraform apply -var env=production"):
        assert g(cmd) == "deny", cmd
    for cmd in ("sdlc deploy REQ-001 staging", "sdlc smoke REQ-001 dev", "kubectl get pods -n production",
                "helm upgrade api ./chart -f values-staging.yaml", "grep -r production docs/"):
        assert g(cmd) is None, cmd
