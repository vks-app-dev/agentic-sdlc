#!/usr/bin/env bash
set -euo pipefail
# Shared by the eval scaffolds: a fresh Agentic SDLC repo, and a way to move a work item to a stage without the
# human-only commands (a scaffold has no person to confirm them; it sets state the way a test does).
new_repo() {
  git init -q -b main
  git config user.email eval@example.com
  git config user.name eval
  printf '%s' '{"project": {"name": "shop", "type": "new"}, "stack": {"languages": ["python"], "commands": {"test": "python -m pytest -q"}}}' > answers.json
  sdlc init --answers answers.json >/dev/null
  rm answers.json
}
sdlc_python() {  # the Python that has agentic_sdlc installed (the uv tool's own interpreter)
  local bin
  bin="$(python3 -c 'import os, shutil; print(os.path.realpath(shutil.which("sdlc")))')"
  "$(dirname "$bin")/python" "$@"
}
advance_to() {  # advance_to REQ-001 planning [complete]: approve earlier gates as a test fixture would
  sdlc_python - "$@" <<'PY'
import sys
from pathlib import Path
from agentic_sdlc import config, state
req, target = sys.argv[1], sys.argv[2]
root = Path(".")
c = config.load(root)
while state.load(root, req)["current_stage"] != target:
    state.start_stage(root, req)
    if state.complete_stage(root, req, c) == state.AWAITING:
        state.approve(root, req, c, "eval-fixture")
if len(sys.argv) > 3:  # finish the target stage too, so it waits at its gate
    state.start_stage(root, req)
    state.complete_stage(root, req, c)
PY
}

new_repo
sdlc new "Monthly statements" >/dev/null
mkdir -p docs/design/REQ-001
cat > docs/design/REQ-001/README.md <<'MD'
# Design: REQ-001 Monthly statements
Status: proposed
## Recommendation
Option A: a statements endpoint that reads transactions for an account and renders the month.
## High-level design
The API reads the account id from the path and returns that account's transactions for the month.
MD
cat > docs/design/REQ-001/api.md <<'MD'
# API and integration design: REQ-001 Monthly statements
## Endpoints
| Method | Path | Purpose | Auth | AC |
| --- | --- | --- | --- | --- |
| GET | /accounts/{account_id}/statements/{month} | the month's statement as JSON or PDF | logged-in user | AC-1 |
## Behaviour
- Authentication and authorization: any logged-in user (session cookie).
MD
git add -A && git commit -qm "REQ-001: design"
