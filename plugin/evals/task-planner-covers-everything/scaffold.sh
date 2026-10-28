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
sdlc new "Refunds" >/dev/null
cat > specs/REQ-001.md <<'MD'
# REQ-001: Refunds
### Acceptance criteria
- **AC-1** (must) Full refund of a card payment
  - Given a paid order
  - When staff refunds it in full
  - Then the payment shows refunded
- **AC-2** (must) A refund cannot exceed the payment
  - Given a payment of 100
  - When staff refunds 150
  - Then the request is rejected with 422
- **AC-3** (should) Partial refund
  - Given a payment of 100
  - When staff refunds 30
  - Then 70 remains refundable
MD
mkdir -p docs/design/REQ-001 docs/plan/REQ-001 app/refunds
printf '# Design\n## Recommendation\nA refunds service and POST /refunds.\n' > docs/design/REQ-001/README.md
printf '# Security\n## Verdict\nOK with mitigations\n- SEC-1: only staff may call POST /refunds\n' > docs/design/REQ-001/security.md
printf '# Operations\n## Work to build\n- OPS-1: refund_issued log event and refunds_total metric\n' > docs/design/REQ-001/operations.md
printf '# Test plan\n## Performance and load\n- TEST-1: load script for 50 refunds per second\n' > docs/plan/REQ-001/test-plan.md
printf '# Release plan\n- REL-1: feature flag refunds_v2, off by default\n' > docs/plan/REQ-001/release-plan.md
advance_to REQ-001 planning
git add -A && git commit -qm "REQ-001: design and plans"
