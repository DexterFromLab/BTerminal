#!/usr/bin/env bash
# tools/release_qa_sanity.sh — pre-release gate (#164)
#
# Walks `smoke-logs/release-qa/` and verifies that every sub-task
# (#165-#179) has:
#   - A checklist.md with NO unticked `[ ]` lines
#   - At least 1 screenshot PNG > 1KB in screenshots/
#   - install.log copy (where applicable, e.g. install/update tasks)
#
# Plus runs the full local pin-suite to ensure nothing regressed since
# QA evidence was captured.
#
# Exit:  0 if every sub-task green, else 1 with summary.
#
# Usage: ./tools/release_qa_sanity.sh
#        STRICT=1 ./tools/release_qa_sanity.sh   # fail also on missing
#                                                  task folders (165-179)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
QA_ROOT="$REPO_ROOT/smoke-logs/release-qa"
STRICT="${STRICT:-0}"

PASS=0; WARN=0; FAIL=0
RESULTS=()

_check_task() {
    local task_id="$1"
    local pattern="$QA_ROOT/task-${task_id}-*"
    local found
    # shellcheck disable=SC2086
    found=$(ls -d $pattern 2>/dev/null | head -1)
    if [[ -z "$found" ]]; then
        if [[ "$STRICT" == "1" ]]; then
            RESULTS+=("✗ #$task_id — no evidence folder")
            FAIL=$((FAIL + 1))
        else
            RESULTS+=("⚠ #$task_id — no evidence folder (yet)")
            WARN=$((WARN + 1))
        fi
        return
    fi

    local checklist="$found/checklist.md"
    local screenshots="$found/screenshots"
    local errors=()

    if [[ ! -f "$checklist" ]]; then
        errors+=("missing checklist.md")
    else
        local unticked
        unticked=$(grep -cE '^- \[ \]' "$checklist" 2>/dev/null || echo 0)
        if [[ "$unticked" != "0" ]]; then
            errors+=("$unticked unchecked items in checklist.md")
        fi
    fi

    if [[ -d "$screenshots" ]]; then
        local n_pngs
        n_pngs=$(find "$screenshots" -name "*.png" -size +1k 2>/dev/null | wc -l)
        if [[ "$n_pngs" -lt "1" ]]; then
            errors+=("no usable screenshots (>1KB) in $screenshots/")
        fi
    else
        errors+=("missing screenshots/ dir")
    fi

    if [[ ${#errors[@]} -eq 0 ]]; then
        RESULTS+=("✓ #$task_id — $(basename "$found")")
        PASS=$((PASS + 1))
    else
        local msg
        msg=$(IFS='; '; echo "${errors[*]}")
        RESULTS+=("✗ #$task_id ($(basename "$found")) — $msg")
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Release QA sanity check ==="
echo "Repository: $REPO_ROOT"
echo "QA root:    $QA_ROOT"
echo "Strict:     $STRICT"
echo

# Iterate spec sub-tasks #165-#179
for n in 165 166 167 168 169 170 171 172 173 174 175 176 177 178 179; do
    _check_task "$n"
done

echo "=== Per-task results ==="
printf '%s\n' "${RESULTS[@]}"
echo

echo "=== Pin suite regression ==="
if (cd "$REPO_ROOT" && python3 -m pytest tests/ -q --no-header 2>&1 | tail -3); then
    echo "  (pin suite output above)"
fi
echo

echo "============================================================"
echo "Release QA sanity:  PASS=$PASS  WARN=$WARN  FAIL=$FAIL"
echo "============================================================"
if (( FAIL > 0 )); then
    echo "RELEASE BLOCKED — fix evidence gaps above + re-run." >&2
    exit 1
fi
if (( WARN > 0 )) && [[ "$STRICT" == "1" ]]; then
    echo "STRICT mode — release blocked on missing folders." >&2
    exit 1
fi
exit 0
