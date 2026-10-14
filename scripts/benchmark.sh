#!/usr/bin/env bash
# benchmark.sh -- Performance benchmark suite for LoL Coach.
#
# Measures the hot paths against the offline data backbone:
#   1. snapshot read
#   2. pure engine scoring (no I/O, no LLM)
#   3. full draft_eval workflow run (writes execution history)
#   4. offline narrator
#
# Requires data/snapshot.db (run `python -m lolcoach.cli fetch` first).
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
N="${BENCH_N:-200}"

echo "==> LoL Coach benchmark (n=$N)"

if [ ! -f "data/snapshot.db" ]; then
    echo "    data/snapshot.db not found."
    echo "    Run: python -m lolcoach.cli fetch --limit 20 && python -m lolcoach.cli build-kg"
    exit 1
fi

if ! $PYTHON -c "import lolcoach" >/dev/null 2>&1; then
    echo "    package not installed. Run: pip install -e '.[dev]'"
    exit 1
fi

# The benchmark entrypoint lives in the package so Windows/macOS/Linux behave
# identically (no platform-specific timing shell tricks).
$PYTHON -m lolcoach.cli benchmark --n "$N"

echo "==> benchmark complete"
