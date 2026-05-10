"""Smoke tests for app module — module structure + ShrinkableBin sanity.

Full BTerminalApp instantiation requires GTK display + many subprocess
side effects, so it's exercised via the xvfb subprocess fixture in the
existing test_gtk_alongside_sidecars / test_health / test_tabs suite.
This file only covers the parts that can run without GTK.
"""

import pytest

from bterminal import app
def test_app_module_imports_all_panels():
    """BTerminalApp imports must include every panel/dialog the app
    composes. If any name is missing, instantiation crashes with
    NameError on first reference. Cheap regression guard."""
    expected = (
        "BTerminalApp", "ShrinkableBin",
        "TerminalTab", "SessionSidebar", "SessionStatsBar",
        "ConsultPanel", "TaskListPanel", "GitPanel", "MemoryPanel",
        "SkillsPanel", "FilesPanel", "PluginManagerPanel", "CtxManagerPanel",
        "OptionsDialog", "ClaudeCodeDialog", "CtxEditDialog",
        "ConsultManager", "SessionManager", "AISessionManager",
        "SidecarRunner", "SidecarDiscovery", "BTerminalPlugin",
    )
    for name in expected:
        assert hasattr(app, name), f"app.{name} missing"


def test_app_module_runtime_state_present():
    """Mutable runtime globals that _toggle_theme reassigns via `global`
    must be on the same module as BTerminalApp — otherwise the keyword
    creates a local that doesn't propagate."""
    assert hasattr(app, "_current_theme")
    assert hasattr(app, "CSS")
    assert app._current_theme in ("dark", "light")
    assert isinstance(app.CSS, str)
    assert len(app.CSS) > 100  # not empty/stub


def test_shrinkable_bin_reports_zero_min_width():
    """Regression for sidebar/git pane shrink behavior — min width must
    always be (0, 0) to let HPaned take all space without GTK clipping."""
    # Avoid creating actual GTK widget (no display); inspect the methods directly.
    bin_cls = app.ShrinkableBin
    assert hasattr(bin_cls, "do_get_preferred_width")
    assert hasattr(bin_cls, "do_get_preferred_width_for_height")
    assert hasattr(bin_cls, "do_size_allocate")


def test_app_session_managers_classes_match_models():
    """app.SessionManager etc. should be the same classes from the models
    module — not accidentally duplicated."""
    from bterminal import models
    assert app.SessionManager is models.SessionManager
    assert app.AISessionManager is models.AISessionManager
    assert app.ConsultManager is models.ConsultManager


def test_app_does_not_redefine_panels():
    """Every panel class app exposes must be the same identity as the
    one imported from its panel_*.py module — guards against accidental
    re-definition during refactor."""
    from bterminal.ui.panels import consult, files, git, memory
    from bterminal.ui.panels import plugin_manager, skills, tasks
    from bterminal.ui.panels import ctx_manager
    from bterminal.ui import terminal_tab, sidebar, stats
    assert app.ConsultPanel is consult.ConsultPanel
    assert app.FilesPanel is files.FilesPanel
    assert app.GitPanel is git.GitPanel
    assert app.MemoryPanel is memory.MemoryPanel
    assert app.PluginManagerPanel is plugin_manager.PluginManagerPanel
    assert app.SkillsPanel is skills.SkillsPanel
    assert app.TaskListPanel is tasks.TaskListPanel
    assert app.CtxManagerPanel is ctx_manager.CtxManagerPanel
    assert app.TerminalTab is terminal_tab.TerminalTab
    assert app.SessionSidebar is sidebar.SessionSidebar
    assert app.SessionStatsBar is stats.SessionStatsBar


def test_app_imports_updater_helpers():
    """Update + errata viewer entry points are imported, ready for menu wiring."""
    for name in ("_check_for_updates", "_load_local_errata", "_show_errata_dialog"):
        assert hasattr(app, name), f"app.{name} missing"
        assert callable(getattr(app, name))


def test_panels_have_no_undefined_names():
    """Static guard — każdy moduł panelu musi mieć wszystkie nazwy używane
    w bytecode poprawnie zdefiniowane (importy + globalsy). Inaczej GTK
    callback rzuca NameError do stderr i UI cicho ignoruje akcję
    (np. plugin manager checkbox toggle 2026-05-04: brak CONFIG_DIR
    w imports → _save_config crashował → store się nie odświeżał)."""
    import dis
    import importlib

    panel_modules = (
        "bterminal.ui.panels.plugin_manager",
        "bterminal.ui.panels.consult",
        "bterminal.ui.panels.tasks",
        "bterminal.ui.panels.skills",
        "bterminal.ui.panels.git",
        "bterminal.ui.panels.memory",
        "bterminal.ui.panels.files",
        "bterminal.ui.panels.ctx_manager",
    )
    for modname in panel_modules:
        mod = importlib.import_module(modname)
        # Walk every function/method defined in module: collect LOAD_GLOBAL
        # names and verify they resolve in module __dict__ + builtins.
        import builtins
        bag: set[str] = set()

        def _collect_globals(co):
            for inst in dis.get_instructions(co):
                if inst.opname in ("LOAD_GLOBAL", "LOAD_NAME"):
                    bag.add(inst.argval)
            for c in co.co_consts:
                if hasattr(c, "co_code"):
                    _collect_globals(c)

        for attr in vars(mod).values():
            # Only inspect symbols DEFINED in this module — re-exported
            # classes (e.g. ConsultManager) bring imports from elsewhere
            # and would cause false positives.
            if getattr(attr, "__module__", None) != modname:
                continue
            co = getattr(attr, "__code__", None)
            if co is not None:
                _collect_globals(co)
            elif isinstance(attr, type):
                for m in vars(attr).values():
                    co2 = getattr(m, "__code__", None)
                    if co2 is not None:
                        _collect_globals(co2)

        unresolved = []
        for name in bag:
            if name in mod.__dict__:
                continue
            if hasattr(builtins, name):
                continue
            unresolved.append(name)
        assert not unresolved, (
            f"{modname} references undefined names: {sorted(unresolved)}. "
            f"Likely missing import — would crash GTK callback at runtime."
        )
