"""Seed a throwaway repo with work items at different stages and simulate agents, for demoing the dashboard.

    uv run python scripts/demo.py /tmp/sdlc-demo          # seed only
    uv run python scripts/demo.py /tmp/sdlc-demo --live   # seed, then keep simulating agent activity
    cd /tmp/sdlc-demo && sdlc dashboard

Agent activity is written through the real plugin hook (plugin/hooks/emit_event.py),
so the dashboard sees exactly what it would see from Claude Code.
"""

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

from agentic_sdlc import config, design, generate, state, testreport

HOOK = Path(__file__).parents[1] / "plugin" / "hooks" / "emit_event.py"

PLAN = [
    {"id": "T1", "title": "Refunds table migration", "files": ["db/migrations/"], "acceptance": ["AC-4"]},
    {"id": "T2", "title": "Refund service logic", "deps": ["T1"], "files": ["app/refunds/service.py"], "acceptance": ["AC-1", "AC-2"]},
    {"id": "T3", "title": "Payment gateway client", "files": ["app/gateway.py"], "acceptance": ["AC-3"]},
    {"id": "T4", "title": "Audit log entries", "files": ["app/audit.py"], "acceptance": ["AC-5"]},
    {"id": "T5", "title": "POST /refunds endpoint", "deps": ["T2", "T3"], "files": ["app/api/refunds.py"], "acceptance": ["AC-1", "AC-2", "AC-3"]},
    {"id": "T6", "title": "End-to-end refund tests", "deps": ["T4", "T5"], "files": ["tests/e2e/"], "acceptance": ["AC-1", "AC-5"]},
]


def hook(root: Path, payload: dict) -> None:
    payload.setdefault("cwd", str(root))
    payload.setdefault("session_id", "demo0000")
    subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True, check=False)


def agent_run(root: Path, agent: str, ref: str, aid: str, actions: list[tuple[str, dict]], stop: str | None) -> None:
    hook(root, {"hook_event_name": "PreToolUse", "tool_name": "Agent",
                "tool_input": {"subagent_type": f"agentic-sdlc:{agent}", "description": ref}})
    hook(root, {"hook_event_name": "SubagentStart", "agent_id": aid, "agent_type": f"agentic-sdlc:{agent}"})
    for tool, inp in actions:
        hook(root, {"hook_event_name": "PostToolUse", "agent_id": aid, "agent_type": f"agentic-sdlc:{agent}",
                    "tool_name": tool, "tool_input": inp, "tool_response": {"exit_code": 0}})
    if stop:
        hook(root, {"hook_event_name": "SubagentStop", "agent_id": aid, "agent_type": f"agentic-sdlc:{agent}",
                    "last_assistant_message": stop})


def seed(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    answers = {"project": {"name": "payments-api", "type": "existing", "compliance": ["soc2"]},
               "stack": {"languages": ["python"], "framework": "fastapi",
                         "commands": {"test": "uv run pytest", "lint": "uv run ruff check ."}},
               "stages": {"requirements": {"gate": ["pm", "tech-lead"]}, "design": {"gate": ["tech-lead"]},
                          "development": {"parallel_coders": 3, "gate": ["peer"]}}}
    cfg = config.build_config(answers)
    config.write(root, cfg)
    generate.scaffold(root, cfg)

    def item(title: str, wtype: str = "feature", links: list | None = None) -> str:
        it = state.new_item(root, cfg, title, wtype, links)
        spec = generate.new_spec(root, it["id"], title, config.work_type(cfg, wtype)["template"],
                                 ", ".join(x["req"] for x in links or []))
        state.add_artifact(root, it["id"], str(spec.relative_to(root)), it["current_stage"])
        return it["id"]

    def through(req: str, stages: int, plan: list | None = None) -> None:
        for i in range(stages):
            state.start_stage(root, req)
            if plan is not None and state.load(root, req)["current_stage"] == "planning":
                state.set_plan(root, req, plan)
            if state.complete_stage(root, req, cfg, "demo") == state.AWAITING:  # a bug's Triage has no gate
                state.approve(root, req, cfg, "ana")

    # REQ-001: in development, a DAG half done, coders running
    r1 = item("Partial refunds for card payments")
    through(r1, 3, PLAN)
    state.start_stage(root, r1)
    for t in ("T1", "T3", "T4"):
        state.update_task(root, r1, cfg, t, "start")
    for t in ("T1", "T4"):
        state.update_task(root, r1, cfg, t, "review")
        state.update_task(root, r1, cfg, t, "done", "merged")
    state.update_task(root, r1, cfg, "T2", "start")
    (root / ".sdlc" / "reviews").mkdir(exist_ok=True)
    (root / ".sdlc" / "reviews" / f"{r1}-T1.md").write_text("Verdict: APPROVE\n")
    (root / ".sdlc" / "reviews" / f"{r1}-T4.md").write_text("Verdict: APPROVE\n- [nit] app/audit.py:12 — name\n")

    # REQ-002: design done, waiting at the design gate
    r2 = item("Export transactions as CSV")
    through(r2, 1)
    state.start_stage(root, r2)
    pack = design.new_pack(root, r2, "Export transactions as CSV")
    text = pack.read_text().replace("## High-level design\n", """## High-level design
```mermaid
flowchart LR
  U[Finance user] -->|GET /exports/transactions.csv| API[Exports router]
  API --> SVC[Export service]
  SVC -->|stream rows| DB[(transactions)]
  SVC -->|chunked CSV| U
```
""")
    pack.write_text(text.replace("- [ ] api.md", "- [x] api.md").replace("- [ ] operations.md", "- [x] operations.md"))
    state.add_artifact(root, r2, str(pack.relative_to(root)))
    for ws in ("api", "operations", "lld", "security"):
        state.add_artifact(root, r2, str(design.workstream(root, r2, "Export transactions as CSV", ws).relative_to(root)))
    design.new_adr(root, r2, "Stream large exports instead of building files")
    state.complete_stage(root, r2, cfg, "Option B (streamed CSV) recommended, 3 tasks, security: OK with mitigations")

    # REQ-003: a change to REQ-001, requirements running
    r3 = item("Refunds can be cancelled within 24 h", "change", [{"rel": "updates", "req": r1}])
    state.start_stage(root, r3)

    # REQ-004: a bug, triaged, its single task blocked after 3 attempts
    r4 = item("Refund totals off by one cent", "bug", [{"rel": "fixes", "req": r1}])
    through(r4, 1)
    state.start_stage(root, r4)
    for note in ("tests fail: test_round_half_even", "lint: unused import", "tests fail: test_round_half_even"):
        state.update_task(root, r4, cfg, "T1", "start")
        state.update_task(root, r4, cfg, "T1", "fail", note)

    # REQ-005: a task, straight to Development
    r5 = item("Upgrade FastAPI to 0.115", "task")

    # REQ-006: a feature in QA, with defects found by the testers
    r6 = item("Monthly statement export")
    through(r6, 3, [{"id": "T1", "title": "Statement service"}, {"id": "T2", "title": "GET /statements", "deps": ["T1"]}])
    state.start_stage(root, r6)
    for t in ("T1", "T2"):
        state.update_task(root, r6, cfg, t, "start")
        state.update_task(root, r6, cfg, t, "done", "merged")
    state.record_checks(root, r6, {"ts": state.events.now(), "head": "4f2a9c1", "passed": True, "results": [
        {"name": n, "status": "passed"} for n in ("lint", "test", "secret scan (built-in)")]})
    through(r6, 1)  # the development gate (CI/CD is off per item by default)
    state.start_stage(root, r6)
    testreport.new_pack(root, r6, "Monthly statement export")
    state.add_defect(root, r6, "Statement for another user's account is returned", "critical", True, ["AC-4"],
                     "qa-security-tester", "GET /statements?account=2 as user 1 returns 200")
    state.add_defect(root, r6, "Totals off by one cent on 31-day months", "high", True, ["AC-2"], "qa-functional-tester")
    state.add_defect(root, r6, "Error message says 'invalid input' for a future month", "low", False, ["AC-5"],
                     "qa-uat-tester")
    state.update_defect(root, r6, "D1", "fix", note="IDOR: owner check missing", by="qa-defect-triager")

    # AI usage as the hook records it when agents stop (tokens per model)
    from agentic_sdlc.stages import default_model
    tier = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
    for req, agent, inp, out, cached in (
            (r1, "req-analyst", 30_000, 9_000, 400_000), (r1, "req-nfr-analyst", 20_000, 6_000, 250_000),
            (r1, "design-architect", 60_000, 18_000, 900_000), (r1, "design-security-reviewer", 30_000, 8_000, 500_000),
            (r1, "plan-task-planner", 25_000, 7_000, 300_000), (r1, "dev-test-writer", 40_000, 22_000, 700_000),
            (r1, "dev-coder", 160_000, 70_000, 3_200_000), (r1, "dev-code-reviewer", 50_000, 12_000, 900_000),
            (r1, "orchestrator", 80_000, 15_000, 2_000_000), (r6, "qa-functional-tester", 40_000, 12_000, 600_000),
            (r6, "qa-defect-triager", 8_000, 2_000, 90_000), (r2, "design-architect", 45_000, 15_000, 700_000)):
        model = tier[default_model(agent)] if agent != "orchestrator" else tier["sonnet"]
        state.events.emit(root, "usage", req=req, agent=agent, source="hook", model=model, input_tokens=inp,
                          output_tokens=out, cache_creation_input_tokens=cached // 10, cache_read_input_tokens=cached)

    # Live-looking agents
    agent_run(root, "dev-coder", f"{r1}/T3 Payment gateway client", "demo-c3",
              [("Write", {"file_path": str(root / "app/gateway.py")}), ("Bash", {"command": "uv run pytest tests/test_gateway.py -q"})], None)
    agent_run(root, "dev-coder", f"{r1}/T2 Refund service logic", "demo-c2",
              [("Edit", {"file_path": str(root / "app/refunds/service.py")})], None)
    agent_run(root, "req-change-impact-analyst", f"{r3} current behaviour and impact", "demo-ra",
              [("Edit", {"file_path": str(root / f"specs/{r3}.md")})], None)
    return {"root": str(root), "items": [r1, r2, r3, r4, r5, r6]}


def live(root: Path) -> None:
    files = {"demo-c3": ["app/gateway.py", "tests/test_gateway.py"], "demo-c2": ["app/refunds/service.py", "app/refunds/models.py"],
             "demo-ra": ["specs/REQ-003.md"]}
    types = {"demo-c3": "dev-coder", "demo-c2": "dev-coder", "demo-ra": "req-change-impact-analyst"}
    print("Simulating agent activity. Ctrl+C to stop.")
    while True:
        aid = random.choice(list(files))
        if random.random() < 0.4 and types[aid] == "dev-coder":
            tool, inp = "Bash", {"command": random.choice(["uv run pytest -q", "uv run ruff check .", "git diff --stat"])}
        else:
            tool, inp = "Edit", {"file_path": str(root / random.choice(files[aid]))}
        hook(root, {"hook_event_name": "PostToolUse", "agent_id": aid, "agent_type": f"agentic-sdlc:{types[aid]}",
                    "tool_name": tool, "tool_input": inp, "tool_response": {"exit_code": 0}})
        time.sleep(random.uniform(1.5, 4))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("path")
    p.add_argument("--live", action="store_true")
    a = p.parse_args()
    target = Path(a.path).resolve()
    if (target / "sdlc.config.yaml").exists():
        sys.exit(f"{target} already has an Agentic SDLC config; pick an empty folder.")
    print(json.dumps(seed(target), indent=2))
    if a.live:
        try:
            live(target)
        except KeyboardInterrupt:
            pass
