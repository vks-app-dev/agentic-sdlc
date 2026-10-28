#!/usr/bin/env python3
"""Claude Code PreToolUse hook: keep humans in charge of the gates.

Denies, for any model-initiated tool call:
  - `sdlc approve` (gates are approved by a person via /sdlc-approve or the dashboard)
  - `sdlc retype` (the kind of work is a human decision: /sdlc-retype or the dashboard)
  - `sdlc defect <REQ> <D> defer|accept` (shipping with a known defect is a human decision)
  - writing .sdlc/state/ by hand (state changes go through the sdlc CLI)
  - anything that deploys to production: `sdlc deployed`, `sdlc deploy|smoke <REQ> <production env>`, the
    configured production deploy/rollback commands (.sdlc/run/guard.json), and deploy tools pointed at prod.
    Agents deploy only to dev and staging; production is always deployed by a person.
  - `git push` and `gh` (except read-only `gh pr view|diff|checks|list`): code reaches GitHub only through
    `sdlc pr`, reviews through `sdlc pr-review`/`pr-reply`; approving and merging PRs are human
Stdlib only; always exits 0 and allows anything it does not recognise.
"""

import json
import re
import sys
from pathlib import Path

RULES = [
    (re.compile(r"(^|[\s;&|(])sdlc\s+approve\b"),
     "Gates are approved by a human. Ask the user to run /sdlc-approve <REQ> or use the dashboard."),
    (re.compile(r"(^|[\s;&|(])sdlc\s+train\s+(approve|deployed)\b|(^|[\s;&|(])sdlc\s+(confirm|reject|skip|deployed|github)\b|(^|[\s;&|(])sdlc\s+task\s+\S+\s+\S+\s+unblock\b"),
     "This is a human decision. Tell the user what you recommend; they decide in the dashboard or their terminal."),
    (re.compile(r"(127\.0\.0\.1|localhost):\d+/api/(items/[^/\s]+/(approve|reject|retype|run|defects|tasks)|pending|runs/)"),
     "The dashboard's actions are for people in the browser, not for agents."),
    (re.compile(r"(^|[\s;&|(])sdlc\s+defect\s+\S+\s+D\d+\s+(defer|accept)\b"),
     "Deferring or accepting a defect is a human decision. Report it, and ask the user to decide in the dashboard."),
    (re.compile(r"(^|[\s;&|(])sdlc\s+retype\b"),
     "The work type is a human decision. Suggest it, and ask the user to run /sdlc-retype <REQ> <type> <reason>."),
    (re.compile(r"git\s+push\b.*(--force\b|-f\b|--force-with-lease)"),
     "Force-push is not allowed by Agentic SDLC."),
    (re.compile(r"(^|[\s;&|(])git\s+push\b"),
     "Pushing is done by `sdlc pr <REQ>` (work-item branches only). Run that instead."),
    (re.compile(r"(^|[\s;&|(])gh\s+(?!pr\s+(view|diff|checks|list)\b)\S+"),
     "Use `sdlc pr`, `sdlc pr-comments`, `sdlc pr-review` and `sdlc pr-reply`. Approving and merging a pull request "
     "is a human decision."),
]
STATE_PATH = re.compile(r"(^|/)\.sdlc/state/")
PROD_WORDS = r"(prod|production|live)"
DEPLOY_TOOL = re.compile(
    r"(^|[\s;&|(])(kubectl|helm|terraform|pulumi|cdk|serverless|sls|vercel|netlify|fly|flyctl|heroku|gcloud|az|aws|"
    r"ansible-playbook|eb|railway|render|wrangler|docker\s+stack|skaffold|argocd|kustomize|npm\s+run|make|just)\b"
    rf"[^;&|]*(\b|[-_=/]){PROD_WORDS}\b", re.I)
PROD_REASON = ("Production is deployed by a person, never by an agent. Agents deploy only to dev and staging "
               "(`sdlc deploy <REQ> staging`). Tell the user the production steps instead.")


def _root(start: str) -> Path | None:
    d = Path(start or ".").resolve()
    for p in (d, *d.parents):
        if (p / "sdlc.config.yaml").exists():
            return p
    return None


def production_rule(cmd: str, cwd: str) -> str | None:
    rules = {"human_only_envs": ["production", "prod", "live"], "commands": []}
    root = _root(cwd)
    if root and (root / ".sdlc" / "run" / "guard.json").exists():
        try:
            rules.update(json.loads((root / ".sdlc" / "run" / "guard.json").read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    envs = "|".join(re.escape(e) for e in rules["human_only_envs"])
    if re.search(r"(^|[\s;&|(])sdlc\s+deployed\b", cmd):
        return "Recording a production deploy is done by the person who deployed it."
    if re.search(rf"(^|[\s;&|(])sdlc\s+(deploy|smoke)\s+\S+\s+({envs})\b", cmd, re.I):
        return PROD_REASON
    if any(c and c in cmd for c in rules["commands"]):
        return PROD_REASON
    if DEPLOY_TOOL.search(cmd) and re.search(r"\b(deploy|apply|release|rollout|upgrade|install|push|up|promote|"
                                              r"publish|sync|--prod)\b", cmd, re.I):
        return PROD_REASON
    return None


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": f"Agentic SDLC: {reason}"}}))


def main() -> None:
    try:
        hook = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    tool, inp = hook.get("tool_name", ""), hook.get("tool_input") or {}
    if tool == "Bash":
        cmd = inp.get("command", "")
        if reason := production_rule(cmd, hook.get("cwd", "")):
            return deny(reason)
        for pattern, reason in RULES:
            if pattern.search(cmd):
                return deny(reason)
    elif tool in ("Write", "Edit", "MultiEdit"):
        if STATE_PATH.search(inp.get("file_path", "")):
            return deny("Work item state is changed only through the `sdlc` CLI, never edited by hand.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
