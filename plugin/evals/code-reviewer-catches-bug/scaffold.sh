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
printf '# REQ-001: Refunds\n### Acceptance criteria\n- **AC-2** (must) A refund cannot exceed the payment\n  - Given a payment of 100\n  - When staff refunds 150\n  - Then the request is rejected\n' > specs/REQ-001.md
mkdir -p app tests && touch app/__init__.py
git add -A && git commit -qm "REQ-001: spec"
git checkout -qb sdlc/REQ-001
git checkout -qb sdlc/REQ-001-T1
cat > app/refunds.py <<'PY'
class RefundError(Exception):
    pass


def refund(payment_amount, refunded_so_far, amount):
    """Return the new refunded total."""
    if amount <= 0:
        raise RefundError("amount must be positive")
    if amount > payment_amount:
        raise RefundError("refund exceeds the payment")
    return refunded_so_far + amount
PY
cat > tests/test_refunds.py <<'PY'
import pytest
from app.refunds import RefundError, refund


def test_refund_over_payment_is_rejected():  # REQ-001 AC-2
    with pytest.raises(RefundError):
        refund(100, 0, 150)
PY
git add -A && git commit -qm "REQ-001/T1: refund limits"
git checkout -q sdlc/REQ-001
