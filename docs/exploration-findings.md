# Exploration findings

Bugs and ergonomic issues discovered by automated testing of the
`feat/sidecar-plugins` build of BTerminal. Two layers:

- **REST exploration** (`tests/test_exploration.py`) — random walker over
  `tests/action_graph/actions.json`, 1000 steps, monitors RSS / FD /
  zombies / state drift. **Found 0 critical, 0 resource leaks.**
- **X11 exploration** (manual + planned `xdotool` test harness) — real
  mouse clicks and keyboard events. **Found 2 real UX bugs** invisible
  to REST.

## Open bugs

### BT-X11-1 · Ctrl+B keyboard shortcut silently broken in terminal focus

- **Severity:** high (advertised in tooltip, doesn't work in primary use case)
- **How to reproduce:**
  1. Open BTerminal, focus is in VTE terminal area (default after open)
  2. Hover over the sidebar toggle button (◀ in `_sidebar_switcher`) →
     tooltip says `Hide sidebar (Ctrl+B)`
  3. Press Ctrl+B
  4. Sidebar does **not** toggle
- **Root cause hypothesis:** VTE terminal widget interprets `Ctrl+letter`
  combinations as ANSI control codes (`Ctrl+B` = `^B`, backward-char in
  bash). VTE consumes the GdkEventKey before it reaches
  `BTerminalApp._on_key_press` (window-level handler at l. ~11060).
- **Failed fix attempt:** intercepting Ctrl+B in `TerminalTab._on_key_press`
  (which connects to `terminal.connect("key-press-event", ...)`) before
  VTE's default handler — see commit dropped during X11 exploration. Still
  did not fire when triggered via `xdotool key --window`. Likely needs
  either a `Gtk.AccelGroup` registered at app level, or VTE's
  `set_input_enabled(False)` toggle around the Ctrl modifier.
- **Workaround:** click the ◀ toggle button on the sidebar instead, or
  call `POST /api/window/toggle_sidebar` via debug-REST.

### BT-X11-2 · Sidebar tab buttons have unpredictable hit-boxes

- **Severity:** low (cosmetic but trips up automation)
- **How to reproduce:**
  1. Open BTerminal — note the two-row sidebar tab strip (Sessions, Ctx,
     Consult, Tasks / Memory, Skills, Files, Plugins, ◀)
  2. Use `xdotool mousemove X Y click 1` with absolute coordinates inside
     the sidebar tab area
  3. Three different (X,Y) inputs spaced ~50px apart can all land on the
     same button
- **Root cause:** buttons are added with `pack_start(btn, True, True, 0)`
  (`expand=True, fill=True`) so each button stretches to fill its row's
  available width. Without rendering, you can't predict the runtime hit
  box from the layout source.
- **Impact:** automated UI testing via `xdotool` mousemove needs either
  Gtk inspector instrumentation, runtime hit-test queries (no GTK API for
  this from outside the process), or REST-driven equivalents (which is
  what we ended up using).

## REST exploration: clean

- 1000 steps, 23/23 actions covered (each 40+ times)
- No memory growth (RSS baseline stable through the run)
- No file-descriptor leaks (< 200 open FDs throughout)
- No zombie subprocesses
- No process crashes or unreachable periods
- 19 soft anomalies — all turned out to be **catalogue (model) imperfections
  in `actions.json`**, not application bugs. The application's behaviour
  was correct in every case; the abstract state model didn't capture two
  acquire paths for sidecars (per-tab refcount vs direct REST start).

## What testing the application from REST cannot detect

Listed for future test design:

- Mouse focus management (which widget receives keystrokes)
- VTE input filtering (Ctrl+letter → ANSI codes)
- GTK accel-group resolution
- Drag-and-drop, hover states, context menus
- Window manager interactions (always-on-top, fullscreen, decorations)
- Touchpad gestures
- Theme-dependent rendering issues

Any further exploration should pair REST coverage with `xdotool` (or
`pyautogui` with `xdisplay`) and reconcile the two streams against
captured screenshots.
