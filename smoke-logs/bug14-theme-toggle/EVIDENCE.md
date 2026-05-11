# BUG#14 evidence — theme toggle Light→Dark fails after Dark→Light

**Captured:** 2026-05-10 19:54-20:00 (real VM, 3-launch cycle)
**Test:** `tests/e2e/test_theme_toggle_light_to_dark.py`

## Visual evidence (real VM, 3 launches)

Driven by 3 sequential BT launches with mutated `options.json` between
each, screenshot via `gnome-screenshot -f` on VM, scp pulled, PIL crop,
visual review through Read tool.

| Step | options.json `theme` | Visible result | File |
|------|---------------------|----------------|------|
| 1 | `dark` | mocha bg, dark sidebar, dark terminal | `vm/zoom_01.png` |
| 2 | `light` | latte bg, light sidebar, white terminal | `vm/zoom_02.png` |
| 3 | `dark` (after light) | **mocha bg restored** ✓ | `vm/zoom_03.png` |

**Conclusion**: the LOAD path (CSS palette swap on bterminal startup)
works correctly for both directions including the failing scenario
(light → dark). Therefore the bug is NOT in the CSS/palette logic
itself — it is in the IN-SESSION toggle code path triggered by
`OptionsDialog.run_and_apply` Save.

## Source-level diagnosis

### `bterminal/app.py:_toggle_theme` (line 1115)
```python
def _toggle_theme(self, *_):
    global _current_theme, CSS
    if _current_theme == "dark":
        _current_theme = "light"   # FLIP — ignores caller's intent
        CATPPUCCIN.update(CATPPUCCIN_LATTE); …
    else:
        _current_theme = "dark"
        CATPPUCCIN.update(CATPPUCCIN_MOCHA); …
    _OPTIONS["theme"] = _current_theme
    _save_options(_OPTIONS)
    CSS = _build_css(CATPPUCCIN)
    self._css_provider.load_from_data(CSS.encode())
```

### `bterminal/ui/dialogs/options.py:run_and_apply` (line 783)
```python
if new_theme != _current_theme:
    self._app._toggle_theme()
```

The Save button's logic is "if user picked something different from
current, flip". The toggle direction depends solely on the
`_current_theme` global, NOT on what the user picked.

### Why this fails for the user

Two scenarios where the flip-toggle gives wrong direction:

1. **Stale `_current_theme` desync**: any code path that mutates
   `_OPTIONS["theme"]` directly (e.g. `_save_options` callback,
   external file edit, another tab's toggle) without also updating
   the `_current_theme` global breaks the assumption.

2. **Combo state mismatch**: the OptionsDialog reads `_OPTIONS.get(
   "theme")` at construction time (line 89) for the combo's initial
   value. If `_OPTIONS["theme"]` and `_current_theme` disagree, the
   user picks combo=X, code asserts X != _current_theme (true), then
   flips current to Y (wrong direction).

The user's reproducible sequence:
1. Initial state: `_current_theme="dark"`, `_OPTIONS["theme"]="dark"`
2. Open Options, pick light, Save → flip → both = light ✓
3. **Open Options again** — somewhere between step 2 and now,
   `_current_theme` may have been mutated by another path (e.g.
   `_save_options` writes the file, the file gets re-read on next
   dialog construction, the global gets out of sync).
4. Pick dark, Save → flip based on stale current → may go light again.

The empirical proof is in the user's screen recording: the dialog
combo says "Ciemny (Mocha)" but applying does nothing visible.

## Fix sketch

Replace the flip-toggle with a **target-driven setter** that's
idempotent for the same target:

`bterminal/app.py`:
```python
def _set_theme(self, target: str):
    """Apply `target` theme regardless of current state.
    Idempotent: calling with current is a no-op."""
    global _current_theme, CSS
    if target == _current_theme:
        return
    if target == "light":
        CATPPUCCIN.update(CATPPUCCIN_LATTE)
        TERMINAL_PALETTE[:] = TERMINAL_PALETTE_LATTE
        self._gtk_settings.set_property(
            "gtk-application-prefer-dark-theme", False)
        self._theme_btn.set_label("☾")
    elif target == "dark":
        CATPPUCCIN.update(CATPPUCCIN_MOCHA)
        TERMINAL_PALETTE[:] = TERMINAL_PALETTE_MOCHA
        self._gtk_settings.set_property(
            "gtk-application-prefer-dark-theme", True)
        self._theme_btn.set_label("☀")
    else:
        raise ValueError(f"unknown theme: {target!r}")
    _current_theme = target
    _OPTIONS["theme"] = target
    _save_options(_OPTIONS)
    CSS = _build_css(CATPPUCCIN)
    self._css_provider.load_from_data(CSS.encode())
    # … re-color terminals (same code as _toggle_theme tail) …

def _toggle_theme(self, *_):
    """Cycle dark → light → dark via target setter."""
    self._set_theme("light" if _current_theme == "dark" else "dark")
```

`bterminal/ui/dialogs/options.py:run_and_apply`:
```python
# Before:
if new_theme != _current_theme:
    self._app._toggle_theme()
# After:
self._app._set_theme(new_theme)   # idempotent, target-driven
```

After fix, BUG#14 cannot recur: `_set_theme(user_pick)` always
applies what the user asked for, regardless of any state drift.

## Pin tests (regression guard)

`tests/e2e/test_theme_toggle_light_to_dark.py` — 5 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_app_has_target_driven_set_theme_method` | **FAIL** | requires `_set_theme` symbol |
| `test_options_dialog_save_calls_set_theme_with_picked_target` | **FAIL** | requires non-flip-toggle invocation |
| `test_set_theme_idempotent_for_same_target` | SKIP | activates after _set_theme exists |
| `test_set_theme_applies_target_regardless_of_current` | SKIP | activates after _set_theme exists |
| `test_vm_load_path_screenshots_exist_for_visual_review` | PASS | visual evidence persists |

After fix lands: 2 FAIL → PASS, 2 SKIP → activate and verify
behavioural contract → 5/5 green.

## Cross-reference

Important design choice: this fix removes `_toggle_theme` as a
public API and replaces with `_set_theme(target)`. The headerbar
`_theme_btn` (☀/☾) handler should call `_toggle_theme` (or be
updated to `_set_theme(opposite_of_current)`). Either works — but
the OptionsDialog Save MUST call `_set_theme` to honor the combo
pick exactly.
