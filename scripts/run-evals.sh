#!/usr/bin/env bash
# Run Agentic SDLC's agent evals with safe defaults. Extra arguments are passed to `claude plugin eval`.
set -euo pipefail
cd "$(dirname "$0")/.."
exec claude plugin eval ./plugin --trust-plugin --scaffold --no-publish --ablation none --runs 1 \
  --allow-tools Bash Write Edit --max-cost-usd "${SDLC_EVAL_MAX_USD:-5}" --threshold "${SDLC_EVAL_THRESHOLD:-0.8}" \
  --concurrency "${SDLC_EVAL_CONCURRENCY:-2}" "$@"
