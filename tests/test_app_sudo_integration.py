"""BUG#31c — BTerminalApp lifecycle integration with SudoAskpassCache.

Verifies:
  * BTerminalApp.__init__ creates self.sudo_askpass (SudoAskpassCache instance)
  * prompt_sudo_password() routes through SudoPasswordDialog.run_and_validate
    and returns its bool verdict
  * _on_delete_event clears the askpass cache so no helper file leaks past
    window close
  * Both classes are re-exported on bterminal.app and bterminal package

BTerminalApp.__init__ pulls a GTK display, so we test methods as unbound
callables on a stand-in `self` rather than constructing the real window.
"""

import inspect
from unittest.mock import MagicMock

from bterminal import app as app_mod
from bterminal.sudo_askpass import SudoAskpassCache


def test_sudo_askpass_class_imported_in_app_module():
    """app.py must hold a top-level reference so __init__ can call it."""
    assert hasattr(app_mod, "SudoAskpassCache")
    assert app_mod.SudoAskpassCache is SudoAskpassCache


def test_sudo_password_dialog_imported_in_app_module():
    """SudoPasswordDialog is imported on app module so tests can monkeypatch it."""
    assert hasattr(app_mod, "SudoPasswordDialog")


def test_package_reexports_sudo_askpass_cache():
    """`from bterminal import SudoAskpassCache` must work."""
    import bterminal
    assert hasattr(bterminal, "SudoAskpassCache")
    assert bterminal.SudoAskpassCache is SudoAskpassCache


def test_init_assigns_sudo_askpass_attribute():
    """BTerminalApp.__init__ source must allocate self.sudo_askpass."""
    src = inspect.getsource(app_mod.BTerminalApp.__init__)
    assert "self.sudo_askpass = SudoAskpassCache()" in src, (
        "BTerminalApp.__init__ must assign self.sudo_askpass = SudoAskpassCache()"
    )


def test_prompt_sudo_password_returns_true_when_dialog_validates(monkeypatch):
    """When SudoPasswordDialog.run_and_validate → True, prompt returns True."""
    fake_dialog_instance = MagicMock()
    fake_dialog_instance.run_and_validate.return_value = True
    fake_dialog_cls = MagicMock(return_value=fake_dialog_instance)
    monkeypatch.setattr(app_mod, "SudoPasswordDialog", fake_dialog_cls)

    fake_self = MagicMock()
    fake_self.sudo_askpass = SudoAskpassCache()

    result = app_mod.BTerminalApp.prompt_sudo_password(fake_self)

    assert result is True
    fake_dialog_cls.assert_called_once_with(fake_self)
    fake_dialog_instance.run_and_validate.assert_called_once_with(
        fake_self.sudo_askpass
    )


def test_prompt_sudo_password_returns_false_when_dialog_cancelled(monkeypatch):
    """When run_and_validate → False (cancel / 3 bad attempts), prompt returns False."""
    fake_dialog_instance = MagicMock()
    fake_dialog_instance.run_and_validate.return_value = False
    fake_dialog_cls = MagicMock(return_value=fake_dialog_instance)
    monkeypatch.setattr(app_mod, "SudoPasswordDialog", fake_dialog_cls)

    fake_self = MagicMock()
    fake_self.sudo_askpass = SudoAskpassCache()

    result = app_mod.BTerminalApp.prompt_sudo_password(fake_self)

    assert result is False


def test_on_delete_event_clears_sudo_askpass():
    """Shutdown signal must drop the cached askpass so /tmp helper goes away."""
    fake_self = MagicMock()
    fake_self.sudo_askpass = MagicMock()
    fake_self._unload_plugins = MagicMock()

    rv = app_mod.BTerminalApp._on_delete_event(fake_self, None, None)

    fake_self.sudo_askpass.clear.assert_called_once_with()
    # delete-event returns False → let Gtk continue destroy chain
    assert rv is False


def test_on_quit_clears_sudo_askpass():
    """File→Quit must run the same cleanup as window close (BUG#31i).

    Earlier menu wired Quit → self.destroy() directly, which emits the
    'destroy' signal but skips 'delete-event' where cache.clear() lived.
    The funnel through _on_quit guarantees the tempfile is removed.
    """
    fake_self = MagicMock()
    fake_self.sudo_askpass = MagicMock()
    fake_self._unload_plugins = MagicMock()

    app_mod.BTerminalApp._on_quit(fake_self)

    fake_self.sudo_askpass.clear.assert_called_once_with()
    fake_self._unload_plugins.assert_called_once_with()
    fake_self.destroy.assert_called_once_with()
