"""Smoke tests for SudoPasswordDialog (BUG#31b).

Sprawdzają, że moduł się importuje, klasa istnieje, ma oczekiwane atrybuty
i sygnaturę run_and_validate — bez konstruowania widgetu (to wymaga DISPLAY
i jest pokryte w xvfb-based component layer na VM).
"""
from __future__ import annotations

import inspect


def test_module_importable():
    """Moduł importuje się bez DISPLAY (top-level Gtk import jest safe)."""
    from bterminal.ui.dialogs import sudo_password

    assert hasattr(sudo_password, "SudoPasswordDialog")


def test_dialog_reexported_from_package():
    """Re-eksport z bterminal/ui/dialogs/__init__.py działa."""
    from bterminal.ui.dialogs import SudoPasswordDialog as Reexported
    from bterminal.ui.dialogs.sudo_password import SudoPasswordDialog as Direct

    assert Reexported is Direct


def test_run_and_validate_method_exists():
    from bterminal.ui.dialogs.sudo_password import SudoPasswordDialog

    method = getattr(SudoPasswordDialog, "run_and_validate", None)
    assert method is not None, "run_and_validate musi być zdefiniowane"
    assert callable(method)


def test_run_and_validate_signature():
    """Sygnatura: (self, cache) → bool."""
    from bterminal.ui.dialogs.sudo_password import SudoPasswordDialog

    sig = inspect.signature(SudoPasswordDialog.run_and_validate)
    params = list(sig.parameters.keys())
    assert params == ["self", "cache"], (
        f"oczekiwana sygnatura (self, cache); jest: {params}"
    )


def test_init_accepts_optional_parent():
    """__init__ przyjmuje opcjonalny parent (default None)."""
    from bterminal.ui.dialogs.sudo_password import SudoPasswordDialog

    sig = inspect.signature(SudoPasswordDialog.__init__)
    params = sig.parameters
    assert "parent" in params
    assert params["parent"].default is None


def test_max_attempts_constant():
    """Loop ma cap 3 prób (zabezpieczenie przed brute-force)."""
    from bterminal.ui.dialogs import sudo_password

    assert sudo_password._MAX_ATTEMPTS == 3
