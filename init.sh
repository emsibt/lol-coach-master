#!/usr/bin/env bash
# init.sh -- Verify the project builds cleanly before starting work.
# Run this after cloning or when resuming work.
#
# Steps: install (if needed) -> ruff -> mypy -> pytest.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

PYTHON="${PYTHON:-python}"

echo "==> LoL Coach init"
echo "    repo: $ROOT"
echo "    python: $($PYTHON --version 2>&1 || echo 'not found')"

if [ ! -d ".venv" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "    note: no .venv detected and no active virtualenv; using the current interpreter"
fi

echo "==> pip install -e '.[dev]'"
$PYTHON -m pip install -e ".[dev]" --quiet

echo "==> ruff check ."
$PYTHON -m ruff check .

echo "==> mypy src"
$PYTHON -m mypy src

echo "==> pytest tests -q"
if [ -d "tests" ] && [ -n "$(ls -A tests 2>/dev/null || true)" ]; then
    $PYTHON -m pytest tests -q
else
    echo "    (no tests yet -- skipping)"
fi

echo "==> init complete: all checks passed"
