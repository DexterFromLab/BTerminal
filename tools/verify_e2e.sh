#!/bin/bash
# verify_e2e.sh — programatyczna weryfikacja running BTerminal'a.
#
# Wymaga: BTerminal odpalony z --debug-rest na :7780. Token w
# ~/.config/bterminal/debug_token.
#
# Exit 0 = wszystko zielone. Exit 1 = któraś weryfikacja padła.
#
# Use cases:
#   1. After every code change na VM: ./tools/verify_e2e.sh
#   2. Before commit: można przed `git commit`
#   3. CI smoke: ten sam skrypt w GitHub Actions
#
# Pattern: for each user flow, REST call → check 2xx + scan stderr log.
# Niezależne od tego co użytkownik kliknie — robimy programatycznie.

set -uo pipefail

TOKEN_FILE="${HOME}/.config/bterminal/debug_token"
BASE_URL="http://127.0.0.1:7780"
STDERR_LOG="${BTERMINAL_STDERR_LOG:-/tmp/bt-new.log}"
FAIL=0

if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "ERROR: $TOKEN_FILE missing — czy BTerminal działa z --debug-rest?"
    exit 1
fi
TOKEN="$(cat "$TOKEN_FILE")"

# ─── helpers ─────────────────────────────────────────────────────────────────

api()    { curl -sf -H "Authorization: Bearer $TOKEN" "$@"; }
api_get(){ api "$BASE_URL$1"; }
api_post(){
    local path="$1"; shift
    api -X POST -H "Content-Type: application/json" "$@" "$BASE_URL$path"
}

# Cursor for stderr log — only check NEW lines per action
LOG_CURSOR=0
if [[ -f "$STDERR_LOG" ]]; then
    LOG_CURSOR=$(stat -c%s "$STDERR_LOG" 2>/dev/null || echo 0)
fi

check_no_errors() {
    local action="$1"
    local size=0
    [[ -f "$STDERR_LOG" ]] && size=$(stat -c%s "$STDERR_LOG" 2>/dev/null || echo 0)
    if [[ $size -le $LOG_CURSOR ]]; then
        return 0
    fi
    local new
    new="$(tail -c $((size - LOG_CURSOR)) "$STDERR_LOG" 2>/dev/null)"
    LOG_CURSOR=$size
    if echo "$new" | grep -qE "Traceback|NameError|AttributeError|TypeError|ImportError|ModuleNotFoundError"; then
        echo "  ✗ stderr errors po '$action':"
        echo "$new" | grep -E "Traceback|NameError|AttributeError|TypeError|ImportError|ModuleNotFoundError" | head -5 | sed 's/^/      /'
        FAIL=1
        return 1
    fi
    return 0
}

action() {
    local desc="$1"; shift
    if "$@" > /tmp/verify_e2e_resp.txt 2>&1; then
        if check_no_errors "$desc"; then
            echo "  ✓ $desc"
        fi
    else
        echo "  ✗ $desc — REST failed"
        cat /tmp/verify_e2e_resp.txt | head -3 | sed 's/^/      /'
        FAIL=1
    fi
}

# ─── checks ──────────────────────────────────────────────────────────────────

echo "=== BTerminal smoke battery ==="

echo "[1] App health:"
action "GET /api/health"  api_get /api/health
action "GET /api/state"   api_get /api/state

echo "[2] Sidebar panels:"
for panel in sessions ctx consult tasks memory skills files plugins; do
    action "sidebar/$panel" api_post /api/window/sidebar/show \
        -d "{\"name\":\"$panel\"}"
done

echo "[3] Tab lifecycle:"
RESP=$(api_post /api/tabs/local 2>/dev/null)
IDX=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['idx'])" 2>/dev/null)
if [[ -n "$IDX" ]]; then
    check_no_errors "tabs/local"
    echo "  ✓ tabs/local (idx=$IDX)"
    action "tabs/$IDX/close" api_post "/api/tabs/$IDX/close"
else
    echo "  ✗ tabs/local — could not parse idx"
    FAIL=1
fi

echo "[4] Plugin / sidecar listing:"
action "GET /api/plugins"  api_get /api/plugins
action "GET /api/sidecars" api_get /api/sidecars

echo "[5] Window screenshot:"
action "GET /api/window/screenshot" api_get /api/window/screenshot

echo "[6] Toggle controls:"
action "toggle_sidebar (1)"   api_post /api/window/toggle_sidebar
action "toggle_sidebar (2)"   api_post /api/window/toggle_sidebar
action "toggle_git_panel (1)" api_post /api/window/toggle_git_panel
action "toggle_git_panel (2)" api_post /api/window/toggle_git_panel

# ─── result ──────────────────────────────────────────────────────────────────

echo ""
if [[ $FAIL -eq 0 ]]; then
    echo "✓ ALL GREEN — żadnej regresji"
    exit 0
else
    echo "✗ FAILED — patrz ${STDERR_LOG} dla pełnego logu"
    exit 1
fi
