import json
import re
from pathlib import Path

import pytest

from agentic_sdlc import config, state, usage
from agentic_sdlc.cli import main
from agentic_sdlc.dashboard.runner import Runner, reported_cost
from agentic_sdlc.stages import default_model

from test_hook import fire, lines


def msg(mid: str, model: str, i: int, o: int, cw: int = 0, cr: int = 0) -> str:
    return json.dumps({"type": "assistant", "message": {"id": mid, "model": model, "usage": {
        "input_tokens": i, "output_tokens": o, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}})


def test_prices_and_cost() -> None:
    assert usage.price("claude-sonnet-5-5") == (2.00, 10.00, 2.50, 0.20)
    assert usage.price("claude-opus-5-5-preview") == usage.PRICES["claude-opus-5-5"]  # longest known prefix
    assert usage.price("claude-haiku-9") == usage.PRICES["claude-haiku-5-5"]  # unknown version: newest of the family
    assert usage.price("gpt-x") is None
    t = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_creation_input_tokens": 0,
         "cache_read_input_tokens": 2_000_000}
    assert usage.cost("claude-sonnet-5-5", t) == pytest.approx(2.00 + 1.00 + 0.40)
    custom = {"pricing": {"claude-sonnet-5-5": {"input": 1.0, "output": 1.0}}}
    assert usage.cost("claude-sonnet-5-5", t, custom) == pytest.approx(1.0 + 0.1 + 0.2)


def test_summary_by_item_stage_agent_model() -> None:
    evs = [
        {"event": "usage", "req": "REQ-001", "agent": "dev-coder", "model": "claude-sonnet-5-5",
         "input_tokens": 1_000_000, "output_tokens": 0},
        {"event": "usage", "req": "REQ-001", "agent": "design-architect", "model": "claude-opus-5-5",
         "output_tokens": 1_000_000},
        {"event": "usage", "req": "REQ-001", "agent": "orchestrator", "stage": "qa", "model": "claude-sonnet-5-5",
         "input_tokens": 500_000},
        {"event": "usage", "req": "REQ-002", "agent": "x", "model": "mystery-model", "input_tokens": 10},
        {"event": "run_finished", "req": "REQ-001", "cost_usd": 3.5},
        {"event": "tool_use", "req": "REQ-001"},
    ]
    s = usage.summarize(evs, None, "REQ-001")
    assert s["total"]["tokens"] == 2_500_000 and s["total"]["cost_usd"] == pytest.approx(2 + 20 + 1)
    assert [r["name"] for r in s["by_stage"]] == ["design", "development", "qa"]  # stage from the agent's prefix
    assert s["by_model"][0]["name"] == "claude-opus-5-5" and s["runs_actual_usd"] == 3.5
    everything = usage.summarize(evs)
    assert {r["name"] for r in everything["by_item"]} == {"REQ-001", "REQ-002"}
    assert next(r for r in everything["by_item"] if r["name"] == "REQ-002")["unpriced_tokens"] == 10


def test_hook_records_subagent_usage_once_per_message(tmp_path: Path) -> None:
    (tmp_path / "sdlc.config.yaml").write_text("version: 1\n")
    session = tmp_path / "sessions" / "s1.jsonl"
    sub = session.with_suffix("") / "subagents" / "agent-a1.jsonl"
    sub.parent.mkdir(parents=True)
    # a transcript repeats each message for every streamed block: count each id once
    sub.write_text("\n".join([msg("m1", "claude-sonnet-5-5", 10, 100, 1000, 5000)] * 3
                             + [msg("m2", "claude-sonnet-5-5", 5, 50), msg("m3", "claude-haiku-5-5", 1, 2)]) + "\n")
    fire(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                    "tool_input": {"subagent_type": "agentic-sdlc:dev-coder", "description": "REQ-001/T2 API"}})
    fire(tmp_path, {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "agentic-sdlc:dev-coder"})
    fire(tmp_path, {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": "agentic-sdlc:dev-coder",
                    "transcript_path": str(session)})
    u = [e for e in lines(tmp_path) if e["event"] == "usage"]
    by_model = {e["model"]: e for e in u}
    assert by_model["claude-sonnet-5-5"]["output_tokens"] == 150 and by_model["claude-sonnet-5-5"]["messages"] == 2
    assert by_model["claude-sonnet-5-5"]["cache_read_input_tokens"] == 5000
    assert {e["agent"] for e in u} == {"dev-coder"} and {(e["req"], e["task"]) for e in u} == {("REQ-001", "T2")}


def test_hook_counts_only_new_session_usage_at_each_stop(tmp_path: Path) -> None:
    (tmp_path / "sdlc.config.yaml").write_text("version: 1\n")
    (tmp_path / ".sdlc" / "run").mkdir(parents=True)
    (tmp_path / ".sdlc" / "run" / "active.json").write_text(json.dumps({"req": "REQ-003", "stage": "design"}))
    t = tmp_path / "main.jsonl"
    t.write_text(msg("m1", "claude-sonnet-5-5", 100, 10) + "\n")
    fire(tmp_path, {"hook_event_name": "Stop", "transcript_path": str(t)})
    with open(t, "a") as f:
        f.write(msg("m1", "claude-sonnet-5-5", 100, 10) + "\n" + msg("m2", "claude-sonnet-5-5", 1, 1) + "\n")
    fire(tmp_path, {"hook_event_name": "Stop", "transcript_path": str(t)})
    fire(tmp_path, {"hook_event_name": "Stop", "transcript_path": str(t)})  # nothing new
    u = [e for e in lines(tmp_path) if e["event"] == "usage"]
    assert [(e["input_tokens"], e["output_tokens"]) for e in u] == [(100, 10), (1, 1)]
    assert {(e["agent"], e["req"], e["stage"]) for e in u} == {("orchestrator", "REQ-003", "design")}


def test_every_agent_file_declares_its_default_model() -> None:
    for p in (Path(__file__).parents[1] / "plugin" / "agents").glob("*.md"):
        text = p.read_text()
        name = re.search(r"^name:\s*(\S+)", text, re.M).group(1)
        assert re.search(rf"^model:\s*{default_model(name)}\s*$", text, re.M), p.name
    assert default_model("design-architect") == "opus" and default_model("rel-notes-writer") == "haiku"
    assert default_model("dev-coder") == "sonnet"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "answers.json").write_text(json.dumps({"project": {"name": "demo", "type": "new"}}))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def test_models_per_agent_are_configurable(repo: Path) -> None:
    c = config.load(repo)
    assert main(["new", "Refunds"]) == 0
    steps = {s["agent"]: s["model"] for s in state.next_action(repo, c, "REQ-001")["steps"]}
    assert steps == {"req-analyst": "sonnet", "req-nfr-analyst": "sonnet", "req-ambiguity-checker": "sonnet"}
    c["models"] = {"orchestrator": "haiku", "agents": {"req-analyst": "opus"}}
    config.write(repo, c)
    c = config.load(repo)
    assert state.next_action(repo, c, "REQ-001")["steps"][0]["model"] == "opus"
    cmd = Runner(repo, lambda: config.load(repo)).command("REQ-001")
    assert cmd[cmd.index("--model") + 1] == "haiku"
    with pytest.raises(config.ConfigError, match="models.agents.dev-coder"):
        config.validate({**c, "models": {"agents": {"dev-coder": "gpt-5"}}})


def test_reported_cost_and_usage_command(repo: Path, capsys: pytest.CaptureFixture) -> None:
    log = repo / "run.log"
    log.write_text('$ claude -p\n\n{"type":"system"}\n{"type": "result", "total_cost_usd": 1.25}\n')
    assert reported_cost(log) == 1.25 and reported_cost(repo / "missing.log") is None
    assert main(["new", "Refunds"]) == 0
    with open(repo / ".sdlc" / "events.jsonl", "a") as f:
        f.write(json.dumps({"ts": "t", "event": "usage", "req": "REQ-001", "agent": "dev-coder",
                            "model": "claude-sonnet-5-5", "input_tokens": 2_000_000, "output_tokens": 100_000}) + "\n")
    capsys.readouterr()
    assert main(["usage", "REQ-001"]) == 0
    out = capsys.readouterr().out
    assert "2.1M tokens, ~$5.00 estimated" in out and "development" in out and "dev-coder" in out
