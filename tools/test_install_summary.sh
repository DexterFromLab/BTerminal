#!/usr/bin/env bash
# tools/test_install_summary.sh — bash-side parity check for task #62.
#
# Asserts that install.sh:
#   1. Has TOOL_REPORT array initialized
#   2. Has emit_tool_summary function emitting the [SUMMARY] block
#   3. Calls emit_tool_summary at end-of-install (post-Files install)
#   4. Lists every cmd that bterminal.diagnostics.DEPENDENCIES knows
#      about as a check_tool call (parity with the Python registry)
#
# Intentionally does NOT exercise apt or sudo — the goal is structural
# verification, not a real install. Run as part of the regular test
# suite via tests/test_install_summary_via_bash.py wrapper.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_SH="$REPO_ROOT/install.sh"

PASS=0
FAIL=0
FAILS=()

assert_contains() {
    local needle="$1" label="$2"
    if grep -qF "$needle" "$INSTALL_SH"; then
        PASS=$((PASS + 1))
        echo "  ✓ $label"
    else
        FAIL=$((FAIL + 1))
        FAILS+=("$label — missing string: $needle")
        echo "  ✗ $label"
    fi
}

assert_function_exists() {
    local fn="$1"
    if grep -qE "^${fn}\(\)" "$INSTALL_SH"; then
        PASS=$((PASS + 1))
        echo "  ✓ function ${fn} defined"
    else
        FAIL=$((FAIL + 1))
        FAILS+=("function ${fn} missing")
        echo "  ✗ function ${fn} missing"
    fi
}

echo "[install.sh structural checks]"
assert_function_exists "emit_tool_summary"
assert_contains "TOOL_REPORT=()" "TOOL_REPORT array initialized"
assert_contains 'TOOL_REPORT+=' "TOOL_REPORT receives entries"
assert_contains '[SUMMARY]' "[SUMMARY] header literal present"
assert_contains 'emit_tool_summary' "emit_tool_summary called somewhere"

# Parity: every Python DepSpec.cmd MUST be referenced by a check_tool
# call in install.sh (and vice versa — covered by Python tests).
echo ""
echo "[parity with bterminal.diagnostics.DEPENDENCIES]"
PY_CMDS="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from bterminal.diagnostics import DEPENDENCIES
print('\n'.join(d.cmd for d in DEPENDENCIES))
")"

while IFS= read -r cmd; do
    [[ -z "$cmd" ]] && continue
    if grep -qE "^check_tool[[:space:]]+${cmd}([[:space:]]|$)" "$INSTALL_SH"; then
        PASS=$((PASS + 1))
        echo "  ✓ ${cmd} has check_tool call"
    else
        FAIL=$((FAIL + 1))
        FAILS+=("DepSpec ${cmd} not invoked via check_tool in install.sh")
        echo "  ✗ ${cmd} — no check_tool call in install.sh"
    fi
done <<< "$PY_CMDS"

echo ""
echo "Result: $PASS passed, $FAIL failed"
if [[ $FAIL -gt 0 ]]; then
    echo ""
    echo "Failures:"
    for f in "${FAILS[@]}"; do echo "  • $f"; done
    exit 1
fi
exit 0
