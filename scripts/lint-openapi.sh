#!/usr/bin/env bash
# Structurally validate generated OpenAPI specs against the 3.1 ruleset in
# vacuum-ruleset.yaml. Aggregated report — every file is linted, failures are
# collected and printed at the end, exit code is non-zero iff at least one file
# fails. Ported from maco-templater-app/scripts/lint-openapi.sh and generalised
# to lint several spec roots (pruefi/, event-bauteil/, event/, bundle/).
#
# Usage: scripts/lint-openapi.sh [DIR ...]
#   No args  -> lint pruefi event-bauteil event bundle (whichever exist)
set -euo pipefail

RULESET="vacuum-ruleset.yaml"
DEFAULT_DIRS=(pruefi event-bauteil event bundle)

if [ "$#" -gt 0 ]; then
    DIRS=("$@")
else
    DIRS=("${DEFAULT_DIRS[@]}")
fi

if [ ! -f "$RULESET" ]; then
    echo "ERROR: ruleset $RULESET is missing" >&2
    exit 2
fi

# Collect existing dirs only — a v<format> branch may legitimately lack a root
# (e.g. a format with prüfis but no composed events).
scan_dirs=()
for d in "${DIRS[@]}"; do
    [ -d "$d" ] && scan_dirs+=("$d")
done
if [ "${#scan_dirs[@]}" -eq 0 ]; then
    echo "ERROR: none of the requested spec dirs exist: ${DIRS[*]}" >&2
    exit 2
fi

passed=0
failed=0
failed_files=()

while IFS= read -r -d '' spec; do
    if vacuum lint --ruleset "$RULESET" --silent --no-banner --no-style "$spec" >/dev/null 2>&1; then
        passed=$((passed + 1))
    else
        failed=$((failed + 1))
        failed_files+=("$spec")
    fi
done < <(find "${scan_dirs[@]}" -name '*.yaml' -print0 | sort -z)

total=$((passed + failed))
echo "OpenAPI 3.1 validation: $passed/$total passed, $failed failed (dirs: ${scan_dirs[*]})"

if [ $total -eq 0 ]; then
    echo "ERROR: no spec files found under ${scan_dirs[*]} — generation likely failed silently" >&2
    exit 2
fi

if [ $failed -gt 0 ]; then
    echo ""
    echo "Failing specs:"
    for spec in "${failed_files[@]}"; do
        echo "  - $spec"
        # NOTE: sed pattern is coupled to vacuum 0.29.4 output format (templater image); revisit on vacuum upgrades
        vacuum lint --ruleset "$RULESET" --no-banner --no-style --errors --details "$spec" 2>&1 \
            | sed -n '/Location/,/total /p' \
            | sed 's/^/      /'
    done
    exit 1
fi
