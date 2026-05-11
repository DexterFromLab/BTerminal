# BUG#5 evidence — OptionsDialog labels overflow horizontally in PL

**Captured:** 2026-05-10 19:07
**Test:** `tests/e2e/test_options_dialog_pl_no_overflow.py`
**Method:** Xvfb + cairo + Gtk.translate_coordinates introspection

## Numerical proof (structural test output)

Driver walks every Gtk.Label inside OptionsDialog after init_locale("pl"),
translates each label's (0,0) to dialog coordinates, asserts it fits.

**7 labels failed the left-edge constraint** (x ≥ 0):

| Label text | x_in_dlg | width | right_edge |
|------------|----------|-------|------------|
| `Język` | -127 | 38 | -89 |
| `Sprawdzaj aktualizacje przy starcie:` | -127 | 240 | 113 |
| `Ogólne` | -127 | 52 | -75 |
| `Terminal` | -127 | 64 | -63 |
| `Wygląd` | -127 | 52 | -75 |
| `AI Providers` | -127 | 78 | -49 |
| (one more) | -127 | … | … |

All 7 labels share `x = -127`. This means the layout's "label column"
(the left-aligned/right-aligned label area in the form grid) was
allocated 127 pixels OUTSIDE the dialog's drawable region — its origin
sits at x=-127.

When the user opens this dialog at the default size on a real X
display, GTK's WM clips the labels to the visible area, producing
the user-reported pattern: the leading characters chopped off on the
left edge ("ktualizacje przy starcie:" instead of "Sprawdzaj
aktualizacje przy starcie:").

## Why it happens

Polish strings are 30-40% longer than English. The form's label-column
SizeGroup was tuned to English minimum widths. When PL translations
are applied:
- SizeGroup recomputes minimum width to accommodate longer labels
- But the dialog's outer width was NOT bumped accordingly
- Net: label column extends past the dialog's left edge (negative x)

This is the same root cause class as BUG#7 (vertical overflow without
scrollbar) — the dialog's allocation doesn't adapt when content grows.

## User-reported screenshot

The user manually documented this on 2026-05-10 with `copied_images/
c2d86a5d1e45.png` showing:
- Left edge cropped: "ktualizacje przy starcie:"
- Bottom-left cropped: "s (Ollama)" (was "Domyślny model AI (Ollama):")
- Right side: "Auto-add vision hint when pasting images into Copilot
  sessions" extends to dialog's right edge

The structural test reproduces this in headless mode without
requiring a display server, making it a permanent regression guard.

## Fix sketch (BUG#5 implementation)

In `bterminal/ui/dialogs/options.py`, where `OptionsDialog.__init__`
calls `set_default_size(...)`:

```python
# Before:
self.set_default_size(540, 600)

# After: bump width minimum + propagate to natural width
self.set_default_size(680, 600)
self.set_size_request(640, 480)  # hard floor
self.set_resizable(True)         # let user expand for longer langs
```

Plus, for the form grid, replace fixed-column SizeGroup with
`Gtk.Grid` `set_column_homogeneous(False)` and let the natural
width drive — or use `set_max_width_chars` on each label so they
wrap instead of overflowing:

```python
for lbl in form_labels:
    lbl.set_line_wrap(True)
    lbl.set_max_width_chars(30)
    lbl.set_xalign(0)
```

After this fix, the structural test's 7 overflows must drop to 0.

## Pin tests (regression guard)

`tests/e2e/test_options_dialog_pl_no_overflow.py` — 4 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_dialog_renders_with_at_least_some_labels` | PASS | sanity |
| `test_no_label_overflows_left_edge_of_dialog` | **FAIL** (7 overflows) | primary BUG#5 guard |
| `test_no_label_overflows_right_edge_of_dialog` | PASS | (no right overflows in headless mode — but user screenshot suggests one in real WM. Test is defensive, not currently triggered.) |
| `test_pl_snapshot_persisted_for_visual_review` | PASS | PNG produced (cairo headless render is white because widgets aren't realized in Xvfb without WM, but the structural assertion above is the real proof) |

After fix lands: `test_no_label_overflows_left_edge_of_dialog` flips
to PASS (0 left-overflow labels).

## Cross-references

- **BUG#6** ("Auto-add vision hint" not translated) — visible in user's
  same screenshot, separate i18n catalog gap.
- **BUG#7** (vertical overflow + no scrollbar) — same dialog, same
  layout-doesn't-adapt-to-content root cause class.
