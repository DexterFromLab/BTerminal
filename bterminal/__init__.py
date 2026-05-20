"""BTerminal — Terminal SSH/Claude Code z panelem sesji.

Public API surface — re-exports the symbols any consumer
(`from bterminal import X`) might need. Lazy imports inside
panel/dialog modules also resolve here.

Submodules:
  config / models / debug_rest / plugin_runtime / sidecar_runtime
  updater / helpers / app
  ctx.{helpers, dialogs, import_export}
  ui.{stats, sidebar, terminal_tab}
  ui.dialogs.{sessions, claude_code, options}
  ui.panels.{consult, ctx_manager, files, git, memory,
             plugin_manager, skills, tasks}

Entry point: `python -m bterminal` (see bterminal/__main__.py).
"""

# ─── Submodule imports for `from bterminal import config` etc. ───────────────
from bterminal import config, debug_rest, helpers, models, plugin_runtime, sidecar_runtime, updater, app  # noqa: F401

# ─── Symbol re-exports (back-compat for `from bterminal import X`) ───────────

from bterminal.config import (
    APP_NAME, APP_VERSION,
    CATPPUCCIN, CATPPUCCIN_LATTE, CATPPUCCIN_MOCHA,
    CLAUDE_SESSIONS_FILE, CONFIG_DIR, CONSULT_CONFIG_FILE,
    CTX_DB, CTX_IMAGES_DIR,
    FONT, KEY_MAP,
    OPTIONS_FILE, PLUGINS_CONFIG_FILE, PLUGINS_DIR, REPO_DIR,
    SCROLLBACK_LINES, SESSIONS_FILE, SSH_PATH,
    TERMINAL_PALETTE, TERMINAL_PALETTE_LATTE, TERMINAL_PALETTE_MOCHA,
    _OPTIONS, _OPTIONS_DEFAULTS, _THEME_COLORS,
    _build_css, _load_options, _parse_color, _save_options,
    _session_color,
    show_error_dialog, show_info_dialog,
)

from bterminal.debug_rest import (
    BTerminalDebugServer, DEBUG_REST_PORT, DEBUG_TOKEN_FILE,
    _load_or_create_debug_token,
    _start_debug_rest_server, _start_idle_watchdog, _stop_debug_rest_server,
)

from bterminal.sidecar_runtime import (
    HealthChecker, SIDECARS_DIR, SidecarDiscovery, SidecarManifest, SidecarRunner,
)
from bterminal.plugin_runtime import BTerminalPlugin

from bterminal.models import (
    AISessionManager, ConsultManager, JsonListManager, SessionManager,
    SessionPasswordCache,
)
from bterminal.sudo_askpass import SudoAskpassCache

from bterminal.ctx.helpers import (
    _GENERIC_SUBDIRS, _collect_claude_log, _is_ctx_available,
    _is_ctx_project_registered, _resolve_ctx_project_name,
    _run_ctx_wizard_if_needed, _smart_project_name,
)
from bterminal.ctx.dialogs import (
    CtxEditDialog, CtxSetupWizard, _CtxEntryDialog, _CtxProjectDialog,
)
from bterminal.ctx.import_export import _CtxExportDialog, _CtxImportDialog

from bterminal.ui.dialogs.sessions import MacroDialog, MacroStepRow, SessionDialog
from bterminal.ui.dialogs.claude_code import ClaudeCodeDialog, _build_intro_prompt
from bterminal.ui.dialogs.options import OptionsDialog

from bterminal.ui.stats import SessionStatsBar
from bterminal.ui.terminal_tab import TerminalTab
from bterminal.ui.sidebar import SessionSidebar

from bterminal.ui.panels.ctx_manager import CtxManagerPanel
from bterminal.ui.panels.consult import ConsultPanel
from bterminal.ui.panels.files import FilesPanel
from bterminal.ui.panels.git import GitPanel
from bterminal.ui.panels.memory import MemoryPanel
from bterminal.ui.panels.plugin_manager import PluginManagerPanel
from bterminal.ui.panels.skills import SkillsPanel
from bterminal.ui.panels.tasks import TaskListPanel

from bterminal.app import BTerminalApp, ShrinkableBin

from bterminal.updater import (
    _check_for_updates, _do_update, _load_local_errata,
    _prompt_update, _restart_bterminal, _show_errata_dialog,
)

from bterminal.helpers import (
    CLAUDE_PATH,
    _BUNDLED_SKILLS_DIR, _GLOBAL_RULES_FILE, _IMAGE_EXTENSIONS,
    _build_sidecar_intro_section, _claude_log_dir,
    _clipboard_get_image_or_path, _clipboard_has_image_or_path,
    _compute_intro_prompt_for_tab, _create_color_combo,
    _delete_ctx_image, _detect_project_description,
    _ensure_images_table, _fetch_ctx_output, _fetch_rules_block,
    _find_claude_path, _list_available_plugins,
    _read_global_rules, _save_ctx_image, _tools_help,
)


# ─── Late helper injection ───────────────────────────────────────────────────
# Push helpers into the panel/dialog module globals once, after all modules
# have finished loading, so bare-name lookup inside their methods resolves
# without per-method lazy imports.

def _inject_helpers_into_panels():
    from bterminal.ctx import dialogs as ctx_dialogs_mod
    from bterminal.ctx import import_export as ctx_import_export_mod
    from bterminal.ui.dialogs import claude_code as dlg_claude_mod
    from bterminal.ui.dialogs import options as dlg_options_mod
    from bterminal.ui.panels import ctx_manager as panel_ctx_mod
    from bterminal.ui.panels import files as panel_files_mod
    from bterminal.ui.panels import memory as panel_memory_mod
    from bterminal.ui.panels import skills as panel_skills_mod
    from bterminal.ui import terminal_tab as panel_tab_mod

    _g = globals()
    sets = {
        ctx_dialogs_mod:        ("_build_intro_prompt", "_detect_project_description"),
        ctx_import_export_mod:  ("_ensure_images_table",),
        dlg_claude_mod:         ("_fetch_ctx_output", "_fetch_rules_block",
                                 "_is_ctx_project_registered", "_list_available_plugins",
                                 "_read_global_rules", "_tools_help"),
        panel_ctx_mod:          ("_ensure_images_table", "_save_ctx_image",
                                 "_delete_ctx_image", "_clipboard_has_image_or_path",
                                 "_clipboard_get_image_or_path"),
        panel_files_mod:        ("_GENERIC_SUBDIRS",),
        panel_memory_mod:       ("_claude_log_dir",),
        panel_skills_mod:       ("_BUNDLED_SKILLS_DIR",),
        panel_tab_mod:          ("CLAUDE_PATH", "_claude_log_dir",
                                 "_clipboard_get_image_or_path", "_clipboard_has_image_or_path",
                                 "_compute_intro_prompt_for_tab", "_find_claude_path",
                                 "_resolve_ctx_project_name", "_save_ctx_image"),
    }
    for module, names in sets.items():
        for n in names:
            if n in _g:
                module.__dict__.setdefault(n, _g[n])

    # _current_theme lives in app.py (mutable runtime state). dlg_options
    # only reads it — forward read-access via module attribute lookup.
    dlg_options_mod.__dict__.setdefault("_current_theme", app._current_theme)


_inject_helpers_into_panels()
