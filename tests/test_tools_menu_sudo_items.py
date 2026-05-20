"""BUG#31e — Tools menu Set/Clear sudo password items.

Verifies:
  * 'Set sudo password…' menu item present in Tools menu source
  * 'Clear sudo password' menu item present
  * _refresh_sudo_menu_sensitivity grays out 'Clear' when cache is empty,
    enables it once SudoAskpassCache.ensure() has succeeded

BTerminalApp.__init__ pulls a GTK display, so menu items are inspected via
source-level checks (no widget construction). Sensitivity logic is unit-
tested by calling the bound method on a MagicMock self.
"""

import inspect
from unittest.mock import MagicMock, patch

from bterminal import app as app_mod
from bterminal.sudo_askpass import SudoAskpassCache


def _menubar_source():
    return inspect.getsource(app_mod.BTerminalApp._build_menubar)


def test_tools_menu_contains_set_sudo_password_item():
    """The Set entry routes through prompt_sudo_password (force-prompt)."""
    src = _menubar_source()
    assert 'N_("Set sudo password…")' in src, (
        "Tools menu must register 'Set sudo password…' as N_() msgid"
    )
    assert "self.prompt_sudo_password" in src, (
        "Set sudo password… must wire to self.prompt_sudo_password"
    )


def test_tools_menu_contains_clear_sudo_password_item():
    """The Clear entry routes through _on_clear_sudo_password (which shows the
    info dialog)."""
    src = _menubar_source()
    assert 'N_("Clear sudo password")' in src, (
        "Tools menu must register 'Clear sudo password' as N_() msgid"
    )
    assert "self._on_clear_sudo_password" in src, (
        "Clear sudo password must wire to self._on_clear_sudo_password"
    )
    # The handler itself must use _() for the user-visible info text.
    handler_src = inspect.getsource(app_mod.BTerminalApp._on_clear_sudo_password)
    assert '_("Hasło sudo wyczyszczone z pamięci")' in handler_src, (
        "Confirmation text must go through _() so locale picks the right form"
    )
    assert "self.sudo_askpass.clear()" in handler_src, (
        "_on_clear_sudo_password must call sudo_askpass.clear() before dialog"
    )


def test_clear_sudo_sensitivity_reflects_cache_state():
    """_refresh_sudo_menu_sensitivity propagates is_set() to the menu item."""
    fake = MagicMock()
    fake.sudo_askpass = SudoAskpassCache()
    fake._clear_sudo_menu_item = MagicMock()

    # Cache empty → menu item disabled
    app_mod.BTerminalApp._refresh_sudo_menu_sensitivity(fake)
    fake._clear_sudo_menu_item.set_sensitive.assert_called_with(False)

    # Populate the cache via the real ensure() path with mocked sudo verify
    with patch(
        "bterminal.sudo_askpass.subprocess.run",
        return_value=MagicMock(returncode=0),
    ):
        assert fake.sudo_askpass.ensure("password") is True
    assert fake.sudo_askpass.is_set() is True

    fake._clear_sudo_menu_item.set_sensitive.reset_mock()
    app_mod.BTerminalApp._refresh_sudo_menu_sensitivity(fake)
    fake._clear_sudo_menu_item.set_sensitive.assert_called_with(True)

    # Clear again → back to disabled
    fake.sudo_askpass.clear()
    fake._clear_sudo_menu_item.set_sensitive.reset_mock()
    app_mod.BTerminalApp._refresh_sudo_menu_sensitivity(fake)
    fake._clear_sudo_menu_item.set_sensitive.assert_called_with(False)


def test_refresh_sensitivity_is_safe_when_menu_item_not_built():
    """Calling refresh before menu construction must not raise (defensive
    guard for code paths that touch state pre-_build_menubar)."""
    fake = MagicMock(spec=["sudo_askpass"])
    fake.sudo_askpass = SudoAskpassCache()
    # No _clear_sudo_menu_item attribute on `fake` — getattr returns None default
    app_mod.BTerminalApp._refresh_sudo_menu_sensitivity(fake)
