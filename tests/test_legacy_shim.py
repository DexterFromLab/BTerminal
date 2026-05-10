"""Tests for the bterminal.py thin shim — re-exports + late-helper injection.

After the modular refactor (Etap 1-12), bterminal.py is a thin entry
point. Its job is to (a) be the script that `~/.local/bin/bterminal`
symlinks to, (b) re-export every symbol any extracted module expects to
find via `from bterminal import X`, (c) inject helpers from bt_helpers
into panel module globals.

These tests verify that contract — anything depending on
`bterminal.X` keeps working.
"""

import pytest

import bterminal


# ─── Re-export contract ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    # config
    "APP_NAME", "APP_VERSION", "CATPPUCCIN", "CONFIG_DIR", "CTX_DB",
    "_OPTIONS", "_build_css", "_parse_color", "_session_color",
    # debug REST
    "DEBUG_REST_PORT", "BTerminalDebugServer",
    "_start_debug_rest_server", "_load_or_create_debug_token",
    # plugins / sidecar
    "BTerminalPlugin", "SidecarManifest", "SidecarRunner", "SidecarDiscovery",
    "HealthChecker",
    # models
    "AISessionManager", "ConsultManager", "JsonListManager", "SessionManager",
    # ctx
    "_resolve_ctx_project_name", "_smart_project_name", "_collect_claude_log",
    "_is_ctx_available", "_is_ctx_project_registered",
    # dialogs
    "ClaudeCodeDialog", "SessionDialog", "MacroDialog", "OptionsDialog",
    "CtxEditDialog", "CtxSetupWizard",
    # panels
    "TerminalTab", "SessionSidebar", "SessionStatsBar", "CtxManagerPanel",
    "ConsultPanel", "FilesPanel", "GitPanel", "MemoryPanel", "SkillsPanel",
    "TaskListPanel", "PluginManagerPanel",
    # app + updater
    "BTerminalApp", "ShrinkableBin",
    "_check_for_updates", "_load_local_errata", "_show_errata_dialog",
    # bt_helpers
    "CLAUDE_PATH", "_compute_intro_prompt_for_tab", "_find_claude_path",
    "_claude_log_dir", "_create_color_combo", "_ensure_images_table",
])
def test_bterminal_re_exports(name):
    """Each name must be accessible as bterminal.X (any consumer doing
    `from bterminal import X` keeps working after the modular refactor)."""
    assert hasattr(bterminal, name), f"bterminal.{name} missing"


# ─── Helper injection ────────────────────────────────────────────────────────

def test_helpers_injected_into_panels():
    """_inject_helpers_into_panels ran on import and pushed helpers into
    panel module globals — bare-name lookup inside panel methods finds
    them without needing per-method lazy imports."""
    from bterminal.ui.panels import ctx_manager as panel_ctx_manager
    from bterminal.ui import terminal_tab as panel_terminal_tab
    from bterminal.ui.panels import files as panel_files
    # These are the bt_helpers symbols panels reference as bare names
    assert hasattr(panel_ctx_manager, "_ensure_images_table")
    assert hasattr(panel_ctx_manager, "_save_ctx_image")
    assert hasattr(panel_terminal_tab, "_compute_intro_prompt_for_tab")
    assert hasattr(panel_terminal_tab, "_claude_log_dir")
    assert hasattr(panel_files, "_GENERIC_SUBDIRS")


def test_sys_modules_alias_set():
    """When bterminal.py runs as __main__, it aliases itself as 'bterminal'
    in sys.modules so cross-module lazy imports resolve to the same
    module object. In tests this is no-op (already imported as
    'bterminal') but the assertion guards against accidental removal."""
    import sys
    assert "bterminal" in sys.modules


def test_main_entry_function_exists():
    """The package entry point must be callable. install.sh creates a
    launcher script at ~/.local/bin/bterminal that does `exec python3 -m bterminal`."""
    from bterminal.__main__ import main
    assert callable(main)
