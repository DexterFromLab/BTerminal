# Task #86 (#158) — View menu E2E

**Date:** 2026-05-08

---

## Deliverable

- `tools/test_view_menu_vm.sh` — driver + REST asserts dla View menu items
- `bterminal/debug_rest.py` — nowy `GET /api/window/state` (read-only)

## /api/window/state endpoint (added)

```json
{
  "sidebar_visible": true,
  "sidebar_active_panel": "sessions",
  "git_visible": false,
  "theme": "dark"
}
```

Read-only — pin test gwarantuje że nie wywołuje `toggle_*` ani
`set_visible_child_name` (NIE mutuje state).

## Sub-tests (8/8 PASS na real VM)

| # | Item | Asercja | Wynik |
|---|------|---------|-------|
| (a) | Ctrl+B toggle sidebar | state.sidebar_visible flips | ✓ true → false |
| (b) | Ctrl+G toggle Git panel | state.git_visible flips | ✓ false → true |
| (c) | View → Toggle theme | state.theme changes | ✓ dark → light |
| (d.1) | View → Sessions | state.sidebar_active_panel = sessions | ✓ |
| (d.2) | View → Ctx | state.sidebar_active_panel = ctx | ✓ |
| (d.3) | View → Consult | state.sidebar_active_panel = consult | ✓ |
| (d.4) | View → Tasks | state.sidebar_active_panel = tasks | ✓ |
| (d.5) | View → Plugins | state.sidebar_active_panel = plugins | ✓ |

## Pin tests — 10/10 ✓ (`tests/test_view_menu_e2e.py`)

| Test | Pin |
|------|-----|
| `test_script_exists_and_executable` | binary + chmod +x |
| `test_script_passes_bash_syntax_check` | bash -n |
| `test_script_documents_all_subtests` | (a)..(d) headers |
| `test_script_uses_window_state_endpoint` | /api/window/state + 4 fields |
| `test_script_drives_via_keyboard_shortcut_for_a_b` | ctrl+b/ctrl+g real accelerators |
| `test_script_drives_via_menu_for_theme_and_panels` | F10 Right Return chain |
| `test_script_tests_all_5_panels` | 5 panel names probed |
| `test_script_uses_live_monitor` | start/tag/stop integration |
| `test_window_state_endpoint_present_in_debug_rest` | route registered |
| `test_window_state_returns_only_safe_data` | NO toggle/set_visible calls — read-only contract |

Combined regression: **181/181** zielono.

## Evidence (real VM, 11 screenshots)

| File | Shows |
|------|-------|
| `00-bt-baseline.png` | start state |
| `01a-sidebar-before.png` | sidebar visible |
| `01a-sidebar-after-1.png` | sidebar hidden after Ctrl+B |
| `02b-git-after.png` | Git panel after Ctrl+G |
| `03c-view-menu-open.png` | View menu open with Toggle theme highlighted |
| `03c-after-toggle-theme.png` | theme switch (REST: dark→light) |
| `04d-panel-sessions.png` | Sessions panel active |
| `04d-panel-ctx.png` | Ctx panel active |
| `04d-panel-consult.png` | Consult panel active |
| `04d-panel-tasks.png` | Tasks panel — full task list visible w/ checkboxes |
| `04d-panel-plugins.png` | Plugins panel active |

## Helpers reused from #157

- `_xfocus_bt` — precyzyjny focus na BT (omija gnome-terminal cwd matches)
- `_rest`, `_rest_load_token`, `_rest_health_ok` — REST helpers
- F10 + Right (move to View) + Return + chained Down + Return

## Verdict

**8/8 PASS** dla menu + shortcuts + panel switchers. Endpoint
`/api/window/state` jest read-only contract (pin test broni). Helpers
reusable w #159-#161.
