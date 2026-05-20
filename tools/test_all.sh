#!/bin/bash
# test_all.sh — universal runner for all automated BTerminal tests.
#
# Wymaga lokalnie:
#   - python3 + pytest + httpx + Pillow (pip install -r requirements-dev.txt)
#   - xvfb (apt install xvfb)         # virtual X display for GTK tests
#   - Vte 2.91 + GTK3 (apt install gir1.2-vte-2.91 gir1.2-gtk-3.0)
#
# Tryby:
#   ./tools/test_all.sh              — fast (bez slow E2E ~10s test_exploration)
#   ./tools/test_all.sh --slow       — full inkluzyjnie z slow E2E
#   ./tools/test_all.sh --slow-only  — TYLKO @pytest.mark.slow (3 testy, ~15s)
#   ./tools/test_all.sh --e2e        — tylko tests/e2e/ (alias --layer e2e)
#   ./tools/test_all.sh --quick      — tylko unit (bez subprocess BTerminal)
#   ./tools/test_all.sh --layer e2e  — tylko tests/e2e/
#   ./tools/test_all.sh --watch      — re-run on file change (wymaga pytest-watch)
#
# Architektura:
#   1. Unit (50)         — czyste funkcje, bez GTK/subprocess        ~0.2s
#   2. Component (~50)   — REST integration via subprocess + xvfb    ~5s
#   3. E2E (~30)         — full flow: vte_capture, smoke battery     ~10s
#   4. Slow (3)          — exploration random walk 1000 steps       ~10s
#   ──────────────────────────────────────────────────────────────────
#   Total fast (1+2+3):  ~16s
#   Total full (1+2+3+4): ~26s

set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:---fast}"
LAYER=""
[[ "${1:-}" == "--layer" ]] && LAYER="$2" && MODE="--fast"

# ─── Pre-flight check ────────────────────────────────────────────────────────

if ! command -v xvfb-run &>/dev/null; then
    echo "✗ xvfb-run brakuje — apt install xvfb"
    exit 1
fi

if ! python3 -c "import pytest, httpx, PIL" 2>/dev/null; then
    echo "✗ python deps missing — pip install pytest httpx Pillow"
    exit 1
fi

# ─── Run ────────────────────────────────────────────────────────────────────

case "$MODE" in
    --fast)
        if [[ -n "$LAYER" ]]; then
            echo "▶ Layer: tests/$LAYER/"
            python3 -m pytest "tests/$LAYER/" -m "not slow" -v
        else
            echo "▶ Fast suite (bez slow): unit + component + e2e"
            python3 -m pytest -m "not slow" --tb=short
        fi
        ;;
    --slow)
        echo "▶ Full suite + slow (random-walk exploration)"
        python3 -m pytest --tb=short
        ;;
    --slow-only)
        echo "▶ Slow tests only (3 expected): exploration + manifests + idle_timeout"
        python3 -m pytest -m slow -v --tb=short
        ;;
    --e2e)
        echo "▶ E2E layer (tests/e2e/) — bterminal_process subprocess fixture"
        python3 -m pytest tests/e2e/ -m "not slow" -v --tb=short
        ;;
    --quick)
        echo "▶ Quick — unit only (no subprocess) + i18n audit"
        python3 -m pytest tests/test_config.py tests/test_models.py \
            tests/test_ctx_helpers.py tests/test_plugin_contracts.py \
            tests/test_updater.py tests/test_session_password_cache.py \
            tests/test_sudo_askpass.py \
            tests/test_sudo_password_dialog.py \
            tests/test_app_sudo_integration.py \
            tests/test_terminal_tab_sudo_branching.py \
            tests/test_tools_menu_sudo_items.py \
            tests/test_app.py tests/test_legacy_shim.py \
            tests/test_license.py tests/test_i18n.py \
            tests/test_translations.py -v
        echo
        echo "▶ i18n audit (hardcoded PL strings outside _())"
        ./tools/check_i18n.py
        ;;
    --watch)
        if ! command -v ptw &>/dev/null; then
            echo "✗ pytest-watch missing — pip install pytest-watch"
            exit 1
        fi
        ptw -- -m "not slow" --tb=short
        ;;
    -h|--help)
        head -25 "$0" | tail -23
        ;;
    *)
        echo "✗ unknown mode: $MODE"
        echo "  use: --fast | --slow | --slow-only | --e2e | --quick"
        echo "       | --layer <e2e|...> | --watch | --help"
        exit 1
        ;;
esac
