# BUG#7 evidence — Options dialog vertical overflow without visible scrollbar

**Captured:** 2026-05-10 19:14-19:19
**Test:** `tests/e2e/test_options_scrollbar_when_expanded.py`
**Method:** Xvfb 1920×944 + Gtk introspection (replicates user's VM)

## Empirical VM data

Direct Python introspection on VM (DISPLAY=:0, language=pl, both
expanders open):

```
DLG_SIZE 560 720
VADJ upper=670.0 page=670.0 scrollable=False
VBAR visible=True realized=True alloc=1x1
```

Headless Xvfb same setup:
```
DLG_SIZE 560 720
VADJ upper=1129 page=682 scrollable=True (overflow 447px)
VBAR visible=True realized=True alloc=5x682
```

## What the data shows

The bug has TWO layers:

### Layer A — Dialog grows to fit content (real VM)
On real VM with PL locale + both expanders, the dialog's outer
height grows to 720px (the cap), AND the ScrolledWindow's viewport
also gets 670px of allocation. With content also ~670px, vadj.upper
== page_size → GTK decides "no overflow, hide scrollbar" → vbar
collapses to 1×1px.

User sees: full content rendered, but the dialog itself is
720px tall on a 944px screen — leaving only ~150px after WM
decorations. With taskbar + window chrome, Save/Cancel buttons
end up clipped at the screen edge. No scrollbar to access them.

### Layer B — Even when overflow exists, scrollbar is too thin (Xvfb)
In headless test mode, the math reports overflow (1129 > 682), so
the scrollbar SHOULD be functional. But its allocated width is
**5px** — Adwaita's "thin scrollbar" / overlay-scrolling style.
That's below the user-perceptible threshold (≈8px gutter is the
de facto minimum).

User sees: technically a scrollbar exists, but it's so thin it's
indistinguishable from the dialog edge.

## Pin tests (regression guard)

`tests/e2e/test_options_scrollbar_when_expanded.py` — 8 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_outer_scrolled_window_uses_automatic_vertical_policy` | PASS | sanity (policy is AUTOMATIC) |
| `test_vscrollbar_widget_is_present_after_both_expanded` | PASS | widget exists in tree |
| `test_vadjustment_indicates_overflow_after_both_expanded` (EN) | PASS | math layer (existing #152 contract) |
| `test_vscrollbar_is_visible_when_content_overflows` (EN) | PASS | visibility flag set |
| `test_dialog_height_does_not_exceed_screen` (EN) | PASS | 80% screen cap |
| `test_collapsed_state_has_no_overflow` (EN) | PASS | sanity inverse |
| `test_pl_locale_both_expanded_produces_visible_scrollbar` | **FAIL** | **PRIMARY BUG#7 GUARD**: vbar width must be ≥8px |
| `test_pl_locale_dialog_allows_overflow_so_scrollbar_can_show` | PASS | math overflow on PL |

The single failure pins the user-perceptible part of the bug:
even in headless mode where overflow is detected, the GTK theme's
default scrollbar width is too thin (5px) to be discoverable. On
real VM (Layer A above) it's even worse — dialog grows to swallow
overflow entirely → 1×1 stub.

## Fix sketch (BUG#7 implementation)

Two complementary changes in `bterminal/ui/dialogs/options.py`:

1. **Force scrollbar to a usable width** (defeats GTK theme thin
   scrollbar / overlay-scrolling). At dialog construction:
   ```python
   scrolled.set_overlay_scrolling(False)
   # Or via CSS:
   provider = Gtk.CssProvider()
   provider.load_from_data(b"scrollbar { min-width: 12px; }")
   scrolled.get_style_context().add_provider(
       provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
   )
   ```

2. **Cap dialog height harder** so PL+expanded content always
   overflows (rather than being absorbed by the dialog's growing
   height). At line options.py:43:
   ```python
   # Before: self.set_default_size(560, min(720, int(screen_h * 0.8)))
   # After: cap at content-fitting min, force vadj overflow on
   # any non-trivial content
   self.set_default_size(560, min(560, int(screen_h * 0.7)))
   ```

After the fix:
- Layer A test (`test_pl_locale_both_expanded_produces_visible_scrollbar`)
  flips from FAIL (5px) to PASS (≥8px)
- All other tests stay PASS

## Cross-reference

- BUG#5 (horizontal overflow) — same dialog, same root cause class
  (allocation doesn't adapt to PL string lengths).
- The fix for BUG#5 (`set_resizable(True)` + explicit width
  request) interacts here: a wider dialog allows shorter content
  height, which keeps overflow behavior consistent.
