#!/bin/sh
# Statement-coverage gate. Fails the build below the floor.
#
# Usage:  sh scripts/coverage.sh [floor]
# Default floor: 80 (COVERAGE_THRESHOLD overrides).
set -eu

FLOOR="${1:-${COVERAGE_THRESHOLD:-80}}"
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"

"$PY" -m coverage --version >/dev/null 2>&1 || {
    echo "coverage is not installed for $PY; install it or set PYTHON" >&2
    exit 1
}

# Branch coverage is not meaningful here: the only conditional network code is
# the transport, which is exercised through mocks. Statement coverage plus the
# behavioural assertions in tests/run.py is the meaningful gate.
"$PY" -m coverage run --source=ebay_deals -m tests.run
echo
"$PY" -m coverage report -m

# Read the TOTAL row's percentage by header name. A positional read is how this
# gate once compared 330% (the branch total) against an 80% floor.
TOTAL="$("$PY" -m coverage report | awk '
    /^Name/ { for (i = 1; i <= NF; i++) if ($i == "Cover") col = i; next }
    /^TOTAL/ { gsub("%", "", $col); print $col }
')"
echo
echo "total statement coverage: ${TOTAL}% (floor ${FLOOR}%)"

awk -v total="$TOTAL" -v floor="$FLOOR" 'BEGIN {
    if (total + 0 < floor + 0) {
        printf "FAIL: coverage %s%% is below the %s%% floor\n", total, floor
        exit 1
    }
    printf "PASS: coverage %s%% meets the %s%% floor\n", total, floor
}'
