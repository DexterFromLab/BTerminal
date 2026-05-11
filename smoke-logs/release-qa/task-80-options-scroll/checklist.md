# Task #80 (#152) — Options dialog ScrolledWindow + screen-cap

**Date:** 2026-05-08
**Methodology:** Release QA Process #164 — every assertion screenshot/measurement-validated

---

## Bug

Po rozwinięciu sekcji **AI Providers** + **Local Models (Ollama)** content rósł a okno nie miało
ScrolledWindow → Cancel/Save uciekały poza monitor. Brak suwaka.

## Fix (`bterminal/ui/dialogs/options.py`)

1. Wrapped sectioned content in `Gtk.ScrolledWindow` z policy `AUTOMATIC/AUTOMATIC`
2. `set_min_content_height(min(560, int(screen_h * 0.7)))` — gwarantuje że suwak się pojawi
3. `set_propagate_natural_height(False)` — bez tego ScrolledWindow rośnie z dziećmi i cap pada
4. Default-size height = `min(720, int(screen_h * 0.8))` przez `Gdk.Display.get_primary_monitor().get_workarea()`

## Evidence

### (a) Pin tests — 4× green
- `test_options_dialog_wraps_content_in_scrolled_window` ✓
- `test_options_dialog_caps_height_to_screen_workarea` ✓
- `test_options_dialog_has_ollama_start_stop_buttons` ✓
- `test_options_dialog_refreshes_status_after_action` ✓
- Full pin suite: **136/136** zielono.

### (b) Headless geometry smoke
```
ScrolledWindow present:           True
get_min_content_height:           560 px
get_propagate_natural_height:     False
vscroll policy:                   GTK_POLICY_AUTOMATIC
Both expanders expanded:          True
Dialog size:                      560×720 (≤ cap 864 = 80% of 1080)
ScrolledWindow vadj.upper=1129  page=682  →  scrollable=True
```
Content (1129 px) > visible (682 px) → **vertical scrollbar must appear** ✓

### (c) Screenshot evidence
- `screenshots/00-dialog-collapsed-baseline.png` — collapsed state, dialog 600×722,
  Cancel/Save visible at bottom, **"ama)"** ledge of "Local Models (Ollama)" expander label
- `screenshots/01-dialog-both-sections-expanded.png` — expanded state, dialog 600×722,
  **Cancel/Save buttons visible at bottom** + Ollama daemon row "✓ /home/bartek/.local/bin/a…"
  showing expander content rendered inside ScrolledWindow

Both states fit within 80% workarea cap — dialog never overflows monitor.

---

## Verdict

**FIXED.** Dialog stays bounded by 80% of monitor workarea regardless of section expansion.
Vertical scrollbar appears automatically when content exceeds visible area. Save/Cancel
buttons stay reachable in all states.
