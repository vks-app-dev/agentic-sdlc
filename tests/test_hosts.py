import asyncio
import json
import re
from pathlib import Path

import pytest

from agentic_sdlc import hosts, human, mcp_server
from agentic_sdlc.cli import main

AGENTS = sorted(p.stem for p in (hosts.PLUGIN / "agents").glob("*.md"))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    answers = {"project": {"name": "shop", "type": "new"}, "integrations": {"hosts": ["claude", "copilot"]},
               "models": {"copilot": {"opus": "Claude Opus 4.5"}}}
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    assert main(["init", "--answers", "answers.json"]) == 0
    return tmp_path


def fm(path: Path) -> dict[str, str]:
    head = path.read_text().split("---", 2)[1]
    return {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in head.strip().splitlines())}


def test_every_agent_is_exported_for_copilot(repo: Path) -> None:
    out = repo / ".github" / "agents"
    assert sorted(p.name.removesuffix(".agent.md") for p in out.glob("*.agent.md")) == sorted([*AGENTS, "sdlc-orchestrator"])
    coder = fm(out / "dev-coder.agent.md")
    assert json.loads(coder["tools"]) == ["edit", "execute", "read", "search", "agentic-sdlc/*"]
    assert coder["user-invocable"] == "false" and "model" not in coder  # sonnet has no Copilot name configured
    assert "No automatic worktree here" in (out / "dev-coder.agent.md").read_text()
    assert fm(out / "design-architect.agent.md")["model"] == '"Claude Opus 4.5"'
    orch = (out / "sdlc-orchestrator.agent.md").read_text()
    assert "agentic-sdlc:" not in orch.split("---", 2)[2].replace("`agentic-sdlc:dev-coder`", "") and "$0" not in orch
    assert '"agent"' in fm(out / "sdlc-orchestrator.agent.md")["tools"]
    run = (repo / ".github" / "prompts" / "sdlc-run.prompt.md").read_text()
    assert "agent: sdlc-orchestrator" in run and "${input:req:REQ-001}" in run
    assert "$ARGUMENTS" not in (repo / ".github" / "prompts" / "sdlc-new.prompt.md").read_text()
    assert json.loads((repo / ".vscode" / "mcp.json").read_text())["servers"]["agentic-sdlc"] == {
        "type": "stdio", "command": "sdlc", "args": ["mcp"]}
    assert "<!-- agentic-sdlc:start" in (repo / "AGENTS.md").read_text()


def test_export_keeps_what_people_wrote(repo: Path, capsys: pytest.CaptureFixture) -> None:
    mine = repo / ".github" / "agents" / "dev-coder.agent.md"
    mine.write_text("---\nname: dev-coder\ndescription: our own\n---\nWe code differently.\n")
    vs = repo / ".vscode" / "mcp.json"
    vs.write_text(json.dumps({"servers": {"other": {"type": "stdio", "command": "x"}}, "inputs": []}))
    capsys.readouterr()
    assert main(["agents", "export", "--host", "copilot"]) == 0
    assert "kept .github/agents/dev-coder.agent.md" in capsys.readouterr().out
    assert mine.read_text().endswith("We code differently.\n")
    data = json.loads(vs.read_text())
    assert set(data["servers"]) == {"other", "agentic-sdlc"} and data["inputs"] == []


def call(name: str, args: dict) -> str:
    result = asyncio.run(mcp_server.server.call_tool(name, args))
    blocks = result[0] if isinstance(result, tuple) else result
    return "".join(getattr(b, "text", "") for b in blocks)


def test_mcp_tools_drive_the_engine(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = {t.name for t in asyncio.run(mcp_server.server.list_tools())}
    assert {"next_action", "complete_stage", "pull_request", "request_human_decision", "deploy"} <= tools
    assert not {"approve", "reject", "retype"} & tools  # human decisions are requests, not tools
    assert '"req": "REQ-001"' in call("new_item", {"title": "Checkout", "type": "bug"})
    assert '"action": "run_stage"' in call("next_action", {"req": "REQ-001"})
    monkeypatch.setattr(human, "interactive", lambda: False)  # an MCP server has no terminal
    assert call("request_human_decision", {"action": "approve", "req": "REQ-001"}).startswith("[exit 3]")
    assert [p["action"] for p in human.pending(repo)] == ["approve"]
    assert "human decision" in call("task", {"req": "REQ-001", "task_id": "T1", "action": "unblock"})
    assert "human decision" in call("update_defect", {"req": "REQ-001", "defect": "D1", "action": "defer"})
    prompt = asyncio.run(mcp_server.server.get_prompt("sdlc_run", {"req": "REQ-007"}))
    text = prompt.messages[0].content.text
    assert "REQ-007" in text and "$0" not in text


def test_config_rejects_unknown_hosts(repo: Path) -> None:
    from agentic_sdlc import config
    c = config.load(repo)
    c["integrations"]["hosts"] = ["claude", "cursorx"]
    with pytest.raises(config.ConfigError, match="cursorx"):
        config.validate(c)
    assert re.search(r"\bcopilot\b", " ".join(hosts.HOSTS))
