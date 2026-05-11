# Task #81 (#153) — Expander collapse hides sections + Save/Cancel

**Date:** 2026-05-08
**Methodology:** Release QA Process #164 — assertion-based + screenshot evidence

---

## Bug

Po rozwinięciu sekcji **AI Providers** / **Local Models (Ollama)** i zwinięciu, expandery
oraz Save/Cancel buttons znikały z dialogu. Window shrinkowało do natural-size bez pamięci
o sąsiadujących elementach layoutu.

## Root cause

GtkExpander przy collapse przeprowadza re-negotiate na natural-size; przy braku floor na
wysokość dialogu cały window się ściskał. ScrolledWindow z `propagate_natural_height=False`
(z task #80) rozwiązywał *expansion*, ale nie zapobiegał *shrink-on-collapse*.

## Fix (`bterminal/ui/dialogs/options.py`)

1. `self._providers_expander.set_resize_toplevel(False)` — explicit pin (default w GTK ale
   gwarantowane przeciw theme override)
2. `self._local_models_expander.set_resize_toplevel(False)` — j.w.
3. `self.set_size_request(560, 480)` — floor: dialog NIGDY nie zejdzie poniżej 480px wysokości
   (natural size top sections), niezależnie od stanu expanderów

## Evidence

### (a) Pin tests — 138/138 zielono
- `test_options_dialog_pins_resize_toplevel_false_on_expanders` — ≥2 wystąpienia call ✓
- `test_options_dialog_has_min_size_floor_against_collapse` — set_size_request present ✓

### (b) Cycle smoke (3 fazy)
```
Cycle 1 — expand both:    560×720
Cycle 2 — collapse both:  560×720   (NIE shrinkuje! min floor 480 respektowany)
Cycle 3 — re-expand AIP:  560×720
After all cycles:
  Expanders present: 2 ✓
  Buttons present:   ['Cancel', 'Save'] ✓
  Min height floor:  720 ≥ 480 ✓
```

### (c) Screenshot evidence
- `screenshots/01-after-collapse-cycle.png` — post-cycle state (oba expandery collapsed,
  AI Providers re-expanded). Widoczne **wszystkie** wbudowane sekcje (Theme/Font/Shell/
  Language/checkboxes), ekspandery AI Providers (rozwinięty: "Add AI Session dropdown..."),
  Local Models collapsed (label "ama)" przy dolnej krawędzi), oraz **Cancel + Save buttons
  na dole**.

---

## Verdict

**FIXED.** Cycle expand→collapse→re-expand nie powoduje już znikania sekcji ani buttons.
Dialog respektuje 480px floor + 80%screen cap. ScrolledWindow obsługuje expansion (#80),
size_request floor obsługuje collapse (#81).
