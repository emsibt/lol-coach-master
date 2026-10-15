#!/usr/bin/env bash
# eval.sh -- Run the agent evaluation suite against a model.
#
# Drives the golden scenarios (evals/scenarios.yaml) through the real agent
# tool-loop and scores them with rule-based checks. Exits non-zero if any
# scenario fails. Requires a configured model (LOL_COACH_LLM_PROVIDER + key).
#
# Usage:
#   bash scripts/eval.sh [extra args...]
#   bash scripts/eval.sh --json data/eval-report.json
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"

if ! $PYTHON -c "import lolcoach" >/dev/null 2>&1; then
    echo "    package not installed. Run: pip install -e '.[dev]'"
    exit 1
fi

$PYTHON -m lolcoach.cli eval "$@"
