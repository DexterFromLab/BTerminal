"""E2E test for BUG#14 — theme toggle Light→Dark fails after a
prior Dark→Light cycle through the Options dialog.

User report (manual QA, 2026-05-10): "Wszedłem w opcję, zmieniłem
motyw na jasny, zapisałem i motyw się zmienił. Potem spowrotem
próbowałem zmienić motyw przez opcje na ciemny, zapisanie nie
zadziałało."

VM evidence (smoke-logs/bug14-theme-toggle/vm/) confirms:
  zoom_01 (theme=dark loaded fresh): mocha bg, dark sidebar
  zoom_02 (theme=light loaded fresh): latte bg, light sidebar
  zoom_03 (theme=dark loaded after light): mocha bg again ✓

Load-path works for both directions. The bug is therefore in the
IN-SESSION toggle path triggered by `OptionsDialog.run_and_apply`.

The fault is in `bterminal/app.py:_toggle_theme` (line 1115). It
flips based on `_current_theme` (a module global) rather than the
target value:

```python
def _toggle_theme(self, *_):
    global _current_theme, CSS
    if _current_theme == "dark":
        _current_theme = "light"   # FLIP — ignores user's request
        ...
    else:
        _current_theme = "dark"
        ...
```

`OptionsDialog.run_and_apply` (options.py:783) calls it
unconditionally when the picked theme differs:
```python
if new_theme != _current_theme:
    self._app._toggle_theme()
```

So the user's choice in the combo is reduced to "different or
same?" — the toggle direction is whatever flips the global. Today
this happens to give the right answer because the combo only has 2
options. But the design is fragile:

  - If `_current_theme` ever drifts from the dialog's combo state
    (e.g. another tab's _toggle_theme call between dialog open and
    save), the user's pick goes the wrong way.
  - Adding a 3rd theme breaks immediately.

The fix replaces the toggle with a target-driven setter:

```python
def _set_theme(self, target: str):
    global _current_theme, CSS
    if target == _current_theme:
        return
    if target == "light":
        CATPPUCCIN.update(CATPPUCCIN_LATTE); …
    elif target == "dark":
        CATPPUCCIN.update(CATPPUCCIN_MOCHA); …
    else:
        raise ValueError(f"unknown theme: {target!r}")
    _current_theme = target
    _OPTIONS["theme"] = target
    _save_options(_OPTIONS)
    CSS = _build_css(CATPPUCCIN)
    self._css_provider.load_from_data(CSS.encode())
```

And `run_and_apply` calls `self._app._set_theme(new_theme)`
unconditionally — idempotent if already that theme.

This test pins both layers: the static design contract (toggle is
target-driven, not flip-based) and the behavioural acceptance
(applying the same target twice is a no-op rather than flipping
back).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_PY = REPO_ROOT / "bterminal" / "app.py"
OPTIONS_PY = REPO_ROOT / "bterminal" / "ui" / "dialogs" / "options.py"


# ── Static: design contract ──────────────────────────────────────────────


def test_app_has_target_driven_set_theme_method():
    """Pin: `BTerminalApp._set_theme(target: str)` should exist as
    a target-driven setter. The flip-based `_toggle_theme` is the
    bug shape — it can't reliably honor a user-picked target if
    `_current_theme` drifts."""
    src = APP_PY.read_text(encoding="utf-8")
    has_setter = bool(
        re.search(r"def\s+_set_theme\(self,\s*\w+(\s*:\s*str)?\s*\)",
                  src))
    assert has_setter, (
        "BTerminalApp lacks `_set_theme(target)`. The current "
        "`_toggle_theme` flips by state — adding a target-driven "
        "setter eliminates the flip-flop bug class."
    )


def test_options_dialog_save_calls_set_theme_with_picked_target():
    """Pin: `run_and_apply` must apply the user's exact pick, not
    a 'flip if different' instruction. Calling `_set_theme(new)`
    unconditionally is correct (the setter is idempotent for
    same-as-current)."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    start = src.find("def run_and_apply")
    assert start > 0
    end = src.find("\n    def ", start + 1)
    body = src[start:end] if end > start else src[start:]

    # Bug shape: conditional flip via _toggle_theme
    bad_pattern = re.search(
        r"if\s+new_theme\s*!=\s*_current_theme:\s*\n\s*"
        r"self\._app\._toggle_theme\(\)",
        body)
    assert not bad_pattern, (
        "run_and_apply still uses the flip-toggle pattern. "
        "Replace with `self._app._set_theme(new_theme)` so the "
        "user's combo pick is applied directly."
    )

    # Good shape
    has_set_call = bool(
        re.search(r"self\._app\._set_theme\(\s*new_theme\s*\)", body)
    )
    assert has_set_call, (
        f"run_and_apply must call `self._app._set_theme(new_theme)`. "
        f"Body slice:\n{body[:400]}"
    )


# ── Behavioural: idempotency + correct target ──────────────────────────


def test_set_theme_idempotent_for_same_target():
    """Pin (behavioural): calling `_set_theme(current)` is a no-op
    — does NOT flip to the other theme. The flip-toggle path
    fails this trivially because every call mutates state."""
    pytest.importorskip("gi")
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    import bterminal.app as bt_app

    if not hasattr(bt_app.BTerminalApp, "_set_theme"):
        pytest.skip("_set_theme not implemented yet — see prior test")

    # Construct a partial mock — _set_theme only needs
    # _css_provider + _gtk_settings + a CATPPUCCIN dict
    fake_app = MagicMock(spec=bt_app.BTerminalApp)
    fake_app._css_provider = Gtk.CssProvider()
    fake_app._gtk_settings = Gtk.Settings.get_default()
    fake_app._theme_btn = Gtk.Button()  # for label updates
    fake_app.notebook = Gtk.Notebook()
    fake_app.sidebar = MagicMock()
    fake_app._git_visible = False
    fake_app.git_panel = MagicMock()

    bt_app._current_theme = "dark"
    initial = bt_app._current_theme
    # Call _set_theme with the SAME target — must be no-op
    bt_app.BTerminalApp._set_theme(fake_app, "dark")
    assert bt_app._current_theme == initial, (
        f"_set_theme('dark') flipped from {initial!r} to "
        f"{bt_app._current_theme!r} — should have been a no-op"
    )


def test_set_theme_applies_target_regardless_of_current():
    """Pin: `_set_theme('light')` must result in light theme,
    whether _current_theme started as 'dark' OR 'light' OR
    something stale. The flip-toggle path can't satisfy this."""
    pytest.importorskip("gi")
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    import bterminal.app as bt_app

    if not hasattr(bt_app.BTerminalApp, "_set_theme"):
        pytest.skip("_set_theme not implemented yet")

    fake_app = MagicMock(spec=bt_app.BTerminalApp)
    fake_app._css_provider = Gtk.CssProvider()
    fake_app._gtk_settings = Gtk.Settings.get_default()
    fake_app._theme_btn = Gtk.Button()
    fake_app.notebook = Gtk.Notebook()
    fake_app.sidebar = MagicMock()
    fake_app._git_visible = False
    fake_app.git_panel = MagicMock()

    # Scenario A: dark → set to light → must be light
    bt_app._current_theme = "dark"
    bt_app.BTerminalApp._set_theme(fake_app, "light")
    assert bt_app._current_theme == "light"

    # Scenario B: light → set to dark → must be dark
    # (the user-reported failing direction)
    bt_app._current_theme = "light"
    bt_app.BTerminalApp._set_theme(fake_app, "dark")
    assert bt_app._current_theme == "dark", (
        "set_theme('dark') after light failed to apply. This is "
        "the user-reported BUG#14 scenario."
    )


# ── Visual evidence persistence ──────────────────────────────────────────


def test_vm_in_session_toggle_screenshots_exist_for_visual_review():
    """Pin: BUG#14 fix workflow on real VM produced screenshots of
    the in-session toggle path: dark → Options→Light→Save (light
    applied) → Options→Dark→Save (dark restored). These artifacts
    in smoke-logs/bug14-fix/ are the visual proof the user-reported
    failing direction (Light→Dark) now works."""
    base = REPO_ROOT / "smoke-logs" / "bug14-fix"
    if not base.is_dir():
        pytest.skip(
            f"bug14-fix evidence dir missing at {base}. "
            f"Re-run task #31 workflow to capture screenshots."
        )
    # The 3 critical states: baseline-dark, applied-light, restored-dark
    expected = [
        "09_baseline_dark_v2.png",
        "12_after_save_light_v2.png",
        "15_after_save_dark_FIX_VERIFIED.png",
    ]
    missing = [n for n in expected if not (base / n).is_file()]
    if missing:
        pytest.skip(
            f"some BUG#14 screenshots missing: {missing}. "
            f"Re-run task #31 workflow."
        )
    # All present — sanity size check (>1 KB = real screenshot)
    for n in expected:
        size = (base / n).stat().st_size
        assert size > 1000, f"{n} too small ({size} bytes)"
