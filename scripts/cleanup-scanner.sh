#!/usr/bin/env bash
# cleanup-scanner.sh -- Check the data directory for stale or inconsistent artifacts.
#
# Reports (does not delete): leftover SQLite WAL/SHM siblings, orphaned execution
# events, gaps in execution seq, zero-byte databases, and stray temp files.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
DATA_DIR="${LOL_COACH_DATA_DIR:-data}"

echo "==> cleanup scan: $DATA_DIR"
findings=0

if [ ! -d "$DATA_DIR" ]; then
    echo "    no data directory -- clean"
    exit 0
fi

# SQLite sidecar files that should not linger after a clean shutdown.
while IFS= read -r f; do
    echo "    WARN stale sqlite sidecar: $f"
    findings=$((findings + 1))
done < <(find "$DATA_DIR" -type f \( -name '*-wal' -o -name '*-shm' \) 2>/dev/null || true)

# Zero-byte databases usually mean an interrupted initialization.
while IFS= read -r f; do
    if [ ! -s "$f" ]; then
        echo "    WARN zero-byte database: $f"
        findings=$((findings + 1))
    fi
done < <(find "$DATA_DIR" -type f -name '*.db' 2>/dev/null || true)

# Stray editor/OS temp files.
while IFS= read -r f; do
    echo "    WARN stray temp file: $f"
    findings=$((findings + 1))
done < <(find "$DATA_DIR" -type f \( -name '*.tmp' -o -name '*~' -o -name '.DS_Store' \) 2>/dev/null || true)

# Deep integrity check of the execution history (seq gaps / orphans), if present.
if [ -f "$DATA_DIR/executions.db" ] && $PYTHON -c "import lolcoach" >/dev/null 2>&1; then
    if ! $PYTHON -m lolcoach.cli scan-executions; then
        findings=$((findings + 1))
    fi
fi

if [ "$findings" -eq 0 ]; then
    echo "    clean: no stale artifacts"
else
    echo "==> $findings finding(s); review above. (This script does not delete anything.)"
fi
