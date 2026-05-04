# BTerminal — plan testowania refaktoryzacji modułowej

**Powiązany dokument:** [`refactor-modular-architecture.md`](./refactor-modular-architecture.md)
**Branch:** `feat/sidecar-plugins` → `feat/modular-refactor`
**Data:** 2026-05-04

Każdy etap refaktoryzacji = ekstrakcja jednego modułu + zestaw testów które potwierdzają, że nic się nie popsuło. Ten dokument rozpisuje **dla każdego modułu osobno**:
- jakie testy mamy już (regresja)
- jakie testy dodajemy
- jak walidować ręcznie
- kryterium "etap zaliczony"

---

## 0. Tooling

### Co jest dostępne

| Narzędzie | Lokalizacja | Zastosowanie |
|-----------|-------------|--------------|
| pytest | `tests/` (16 plików, 24 testy) | regresja per moduł |
| debug REST API | `--debug-rest` flag | self-test harness, screenshots, simulate prompts |
| action-graph runner | `tests/action_graph/` | scenariusze E2E na UI |
| random-walk explorer | `tests/test_exploration.py` | fuzzing UI (1000 kroków) |
| Xvfb | `conftest.py` fixture | testy headless |
| screenshot diff | `_via_glib_idle` + PIL | wizualna regresja paneli |
| manualne smoke | VM michal_mint | krytyczne ścieżki użytkownika |

### Macierz typów testów

Dla każdego modułu używamy mieszanki:
- **Unit** — pojedyncze funkcje/klasy w izolacji (głównie `config`, `models`, `ctx/db`)
- **Integration** — moduł w kontekście aplikacji, mockowanie zewnętrznych zależności (REST, sidecars)
- **Smoke** — czy aplikacja w ogóle startuje po ekstrakcji
- **Visual regression** — screenshot przed/po (md5 lub diff)
- **Manual** — kliknięcia użytkownika w VM

### Komenda referencyjna

```bash
# Pełna regresja (po każdym etapie):
pytest -v -m "not slow"          # 22 testów, ~8s

# Regresja + slow E2E (przed mergiem etapu):
pytest -v                         # 24 testów, ~30s

# Konkretny moduł:
pytest tests/test_<module>.py -v
```

---

## 1. Per-module test plans

### Etap 1 — `bterminal/config.py`

**Co przenosimy** (317 L):
`_load_options`, `_save_options`, `OPTIONS`, `OPTIONS_FILE`, `CONFIG_DIR`, `CATPPUCCIN`, `TERMINAL_PALETTE`, `CSS`, `_build_css`, `_parse_color`, `_session_color`, `show_error_dialog`, `show_info_dialog`.

**Regresja (istniejące):**
- żaden istniejący test nie dotyka config bezpośrednio — pełna pytest suite jako sanity check że import nie pęka

**Nowe testy (`tests/test_config.py`):**

```
test_options_default_when_missing       — _load_options() bez pliku zwraca defaults
test_options_roundtrip                  — _save_options() → _load_options() identycznie
test_options_corrupt_json_falls_back    — uszkodzony JSON → defaults, brak crash
test_parse_color_hex                    — "#1e1e2e" → Gdk.RGBA poprawne RGB
test_parse_color_invalid_returns_none   — "garbage" → None
test_session_color_deterministic        — ten sam input = ten sam kolor
test_session_color_distribution         — 100 różnych nazw → ≥80 różnych kolorów (collision rate)
test_build_css_contains_palette         — _build_css() output zawiera CATPPUCCIN base
test_build_css_no_undefined_vars        — output nie ma `--{undefined}`
```

**Manual smoke:**
- start `bterminal` → CSS aplikuje (kolor tła paneli, kolor accent)
- otwórz Options dialog → wartości się ładują z `~/.config/bterminal/options.json`
- zmień opcję, zamknij → plik zapisany

**Kryterium zaliczenia:**
- [x] 9/9 nowych testów green
- [x] 22/22 istniejących testów green
- [x] manual smoke przechodzi
- [x] `bterminal` startuje, GUI bez różnic wizualnych (screenshot baseline vs po-refactor identyczny lub różnice tylko z odświeżenia)

---

### Etap 2 — `bterminal/models.py`

**Co przenosimy** (217 L):
`JsonListManager`, `SessionManager`, `ClaudeSessionManager`, `ConsultManager`.

**Regresja:**
- `test_per_tab_plugins.py` — używa SessionManager pośrednio (claude_manager attached do app)
- `test_intro_prompt.py` — ConsultManager w intro promptcie

**Nowe testy (`tests/test_models.py`):**

```
test_json_list_manager_add_remove       — add → contains → remove → empty
test_json_list_manager_persists         — close+reopen → ten sam stan
test_json_list_manager_schema_migration — stary schemat (brak field X) → nowy z defaultem
test_session_manager_unique_names       — drugi save z tą samą nazwą = update, nie duplicate
test_claude_session_loads_existing      — załaduj projekt z istniejącym claude_log/
test_consult_manager_default_model      — set_default → get_default zwraca to
test_consult_manager_disabled_models    — disabled flag respektowany przy `models()` call
```

Fixture: `tmp_path` per test, mockujemy `CONFIG_DIR` przez `monkeypatch`.

**Manual smoke:**
- dodaj sesję SSH przez sidebar → zapisz → restart bterminal → sesja widoczna
- otwórz Consult panel → fetch modeli → ustaw default → restart → default trzyma się

**Kryterium zaliczenia:**
- [x] 7/7 nowych testów
- [x] 22/22 regresja
- [x] 2 manualne smoke

---

### Etap 3 — `bterminal/debug_rest.py`

**Co przenosimy** (836 L):
`BTerminalDebugHandler`, `BTerminalDebugServer`, `_audit_log`, `_via_glib_idle`, wszystkie 26 `_route_*`, `_start_debug_rest_server`, `_stop_debug_rest_server`, `_start_idle_watchdog`, `_generate_debug_token`, `_load_or_create_debug_token`, `_rotate_debug_log_if_needed`.

**Regresja (już mamy KOMPLET):**
- `test_health.py` — `/api/health`
- `test_auth.py` — token whitelist, 401 dla bad token
- `test_audit_log.py` — wpisy do `~/.cache/bterminal/debug-rest.log`
- `test_idle_timeout.py` — auto-shutdown po `BTERMINAL_DEBUG_IDLE_TIMEOUT`
- `test_quit.py` — `POST /api/quit?confirm=true`
- `test_tabs.py` — open/close/feed
- `test_screenshot_endpoint.py` — `GET /api/window/screenshot`
- `test_intro_prompt.py` — `/api/tabs/{idx}/intro_prompt`

**Nowe testy:** brak — pełne pokrycie już jest. Dodajemy tylko jedną asercję importu:

```
test_module_imports               — `from bterminal.debug_rest import BTerminalDebugServer` works
```

**Manual smoke:**
- `bterminal --debug-rest` → title bar `[DEBUG-REST :7780]`
- `curl -H "X-Debug-Token: $(cat ~/.config/bterminal/debug_token)" http://127.0.0.1:7780/api/health` → 200
- czerwony pasek 2px nad notebookiem widoczny

**Kryterium zaliczenia:**
- [x] 22/22 regresja (krytyczne — to jest main test suite tego modułu)
- [x] manual smoke
- [x] visual marker (czerwony pasek) bez zmian

---

### Etap 4 — `bterminal/plugins/` (in-process + sidecar)

**Co przenosimy** (~280 L kontrakt + 247 L sidecar):
- `BTerminalPlugin` ABC (kontrakt in-process, w monolicie ~L9700)
- `_load_plugins` (loader importlib, ~L9705)
- `SidecarManifest`, `SidecarDiscovery`, `SidecarRunner`, `HealthChecker`

**Struktura targetowa:**
```
bterminal/plugins/__init__.py        # BTerminalPlugin ABC
bterminal/plugins/inproc_loader.py   # _load_plugins
bterminal/plugins/sidecar/manifest.py
bterminal/plugins/sidecar/discovery.py
bterminal/plugins/sidecar/runner.py
bterminal/plugins/sidecar/health.py
```

**Regresja (już mamy):**
- `test_manifests.py` — 5 manifestów referencyjnych ładuje się + walidacja schemy
- `test_sidecar_discovery.py` — Discovery widzi wszystkie manifesty
- `test_sidecar_lifecycle.py` — start → /health → stop, refcount
- `test_per_tab_plugins.py` — per-tab enabled, hot toggle
- `test_hot_toggle.py` — włącz/wyłącz plugin w runtime, brak side-effectów GUI
- `test_gtk_alongside_sidecars.py` — sidecary nie psują głównego okna

**Nowe testy (`tests/test_plugin_contracts.py`):**

```
test_inproc_plugin_abc_methods         — BTerminalPlugin ma activate/deactivate/get_keyboard_shortcuts/get_session_context/on_sidebar_shown
test_inproc_loader_finds_create_plugin — fake plugin z create_plugin(app) → instancja zwracana
test_inproc_loader_skips_invalid       — plugin bez create_plugin → log warning, kontynuuje
test_inproc_loader_isolates_failures   — plugin który raise w create_plugin → reszta plików nadal ładuje się
test_sidecar_manifest_required_fields  — brak `plugin_address` → ValidationError
test_sidecar_runner_no_double_start    — start → start → drugi no-op (refcount++)
test_health_checker_timeout            — backend nie odpowiada → status="unhealthy"
```

Fixture: tmp dir z fake plugin'em (RemoteControll-like, minimal).

**Manual smoke:**
- start `bterminal` z RemoteControll w `~/.config/bterminal/plugins/` → panel RC widoczny w sidebar
- `curl POST /api/sidecars/btmsg/start` (jeśli btmsg dostępny) → process w `ps`, `/health` 200
- `curl POST /api/sidecars/btmsg/stop` → process zniknął

**Kryterium zaliczenia:**
- [x] 7 nowych testów
- [x] 22 regresja (sidecar suite to core)
- [x] RemoteControll dalej działa (jeśli zainstalowany na VM)
- [x] btmsg sidecar dalej startuje (slow test `test_btmsg_starts_and_health` na laptopie)

---

### Etap 5 — `bterminal/ui/panels/` (8 paneli)

**Co przenosimy** (1 890 L):
- `ui/stats.py` — SessionStatsBar, _SessionStatsReader (296 L)
- `ui/panels/consult.py` — ConsultPanel (761 L)
- `ui/panels/tasks.py` — TaskListPanel (512 L)
- `ui/panels/git.py` — GitPanel (618 L)
- `ui/panels/memory.py` — MemoryPanel (568 L)
- `ui/panels/skills.py` — SkillsPanel (339 L)
- `ui/panels/files.py` — FilesPanel (510 L)
- `ui/panels/plugin_manager.py` — PluginManagerPanel (315 L)

**Każdy panel = osobny commit + osobny test.**

**Strategia:** GUI testing jest drogie. Używamy:
1. **Smoke przez debug REST** — `POST /api/window/sidebar/show?panel=X` → `GET /api/window/screenshot` → diff vs baseline
2. **Headless instantiation** — `Panel(fake_app)` w pytest, sprawdź że `.activate()` nie crashuje
3. **Manual** — operator klika podstawowy flow

**Regresja:**
- `test_gtk_alongside_sidecars.py` — pełen boot z wszystkimi panelami
- `test_exploration.py` — random walk dotyka panele

**Nowe testy (per-panel: `tests/test_panel_<name>.py`):**

Wzorzec dla każdego panelu:

```
test_<panel>_imports               — moduł się importuje, klasa istnieje
test_<panel>_instantiates          — Panel(fake_app) bez raise
test_<panel>_activate              — .activate() zwraca Gtk.Widget
test_<panel>_deactivate_idempotent — .deactivate() x2 nie crashuje
test_<panel>_screenshot_baseline   — uruchom w xvfb, otwórz panel, screenshot porównany z baseline (pierwszy run = save)
```

Specyficzne dla każdego panelu:

| Panel | Dodatkowy test |
|-------|----------------|
| `consult` | `test_consult_fetch_models_offline` — bez API key, fetch fails gracefully |
| `tasks` | `test_tasks_load_db` — sqlite z tasks → lista renderuje |
| `git` | `test_git_panel_no_repo` — w katalogu nie-git → "no repo" message |
| `memory` | `test_memory_lists_rules` — fake `~/.config/bterminal/memory/` → reguły widoczne |
| `skills` | `test_skills_lists_skills` — fake skills dir → lista |
| `files` | `test_files_walks_project` — tmp project → drzewo plików |
| `plugin_manager` | `test_plugin_manager_lists_inproc` — fake plugin folder → wpis w liście |
| `stats` | `test_stats_bar_increments` — `_stats_bar.increment_prompt()` zmienia counter |

**Manual smoke (per panel):**

| Panel | Co kliknąć |
|-------|-----------|
| `consult` | otwórz, fetch models, set default, send query |
| `tasks` | otwórz, dodaj task, claim, mark done |
| `git` | w repo → status, log, diff |
| `memory` | otwórz, dodaj regułę, edytuj |
| `skills` | otwórz, sprawdź listę bundled skills |
| `files` | otwórz, przejdź do pliku, otwórz w editor |
| `plugin_manager` | otwórz, włącz/wyłącz plugin |
| `stats` | sprawdź że counter rośnie po send Claude prompt |

**Kryterium zaliczenia (per panel):**
- [x] 5 unit testów panelu green
- [x] 22 regresja green
- [x] manual smoke przeszedł
- [x] screenshot baseline = po-refactor (lub explicite zaakceptowana zmiana)

---

### Etap 6 — `bterminal/ui/sidebar.py` + `bterminal/ui/terminal_tab.py`

**Co przenosimy** (1 563 L):
- `SessionSidebar` (798 L) → `ui/sidebar.py`
- `TerminalTab` (765 L) → `ui/terminal_tab.py`

**Regresja:**
- `test_tabs.py` — open/close (kluczowe!)
- `test_per_tab_plugins.py` — plugin enabled per-tab
- `test_intro_prompt.py` — intro prompt na nowym tabie
- `test_action_graph.py` — pełne scenariusze UI z action_graph

**Nowe testy:**

```
tests/test_sidebar.py:
  test_sidebar_lists_sessions           — sesje SSH + Claude widoczne na liście
  test_sidebar_add_session              — fixture session → po dodaniu pojawia się
  test_sidebar_edit_session             — edit dialog → zapisz → lista się aktualizuje
  test_sidebar_delete_session           — delete → potwierdzenie → znika

tests/test_terminal_tab.py:
  test_tab_imports                      — moduł importuje
  test_ssh_tab_spawns                   — fake SSH command (`bash -c sleep`) → tab się tworzy
  test_claude_tab_spawns                — claude_log dir tworzony, intro prompt zapisany
  test_tab_close_kills_subprocess       — close tab → child process gone w 5s
  test_tab_macro_expand                 — macro w tab → wpisany w VTE
  test_tab_inject_pending               — _maybe_inject_rules → _inject_pending populated
```

**Manual smoke:**
- otwórz tab SSH na localhost → prompt widoczny
- zamknij tab SSH → process zniknął (`ps -ef | grep ssh`)
- otwórz tab Claude Code → claude startuje, intro prompt zaaplikowany
- zamknij tab Claude → claude exits, exec bash fallback
- prawym na sesji → edit, delete

**Kryterium zaliczenia:**
- [x] 4 testy sidebar + 6 testów tab green
- [x] 22 regresja
- [x] action-graph pełny scenariusz przechodzi
- [x] manual: SSH + Claude tab lifecycle bez zacięć

---

### Etap 7 — `bterminal/ui/dialogs/` + `bterminal/ctx/`

**Co przenosimy** (1 518 L):

`ui/dialogs/`:
- `sessions.py` — SessionDialog (95 L), MacroDialog (153 L)
- `claude_code.py` — ClaudeCodeDialog (223 L)
- `options.py` — OptionsDialog (111 L)

`ctx/`:
- `wizard.py` — CtxSetupWizard (329 L)
- `editor.py` — CtxEditDialog (162 L) + _CtxEntryDialog (50 L) + _CtxProjectDialog (72 L)
- `import_export.py` — _CtxImportDialog (285 L) + _CtxExportDialog (251 L)
- `detect.py` — project root detection helpers
- `images.py` — image management

**Regresja:**
- `test_intro_prompt.py` — generowanie intro promptu używa Ctx
- ekstrakty z `test_per_tab_plugins.py`

**Nowe testy:**

```
tests/test_dialogs.py:
  test_session_dialog_validates       — pusta nazwa → save disabled
  test_session_dialog_roundtrip       — fill → save → SessionManager.add() called z poprawnymi danymi
  test_macro_dialog_supports_multiline— wieloliniowy macro zachowuje newline
  test_claude_code_dialog_browse      — Browse button → dir picker (mock GtkFileChooser)
  test_options_dialog_persists        — change opcję → save → _OPTIONS aktualizowane

tests/test_ctx_wizard.py:
  test_wizard_init_creates_db          — wizard finish → sqlite DB istnieje, schemat OK
  test_wizard_default_entries          — finish → seed entries dla project type (Python/JS/etc)
  test_wizard_skip_existing_project    — kontekst już istnieje → wizard wykrywa, oferuje merge

tests/test_ctx_editor.py:
  test_entry_dialog_add                — add new key → DB ma wpis
  test_entry_dialog_edit               — edytuj wartość → DB zaktualizowane
  test_entry_dialog_delete             — delete → wpis zniknął

tests/test_ctx_import_export.py:
  test_export_roundtrip                — export → import w tmp dir → identyczny stan DB
  test_export_excludes_shared          — eksport per-project nie zawiera shared entries
  test_import_merge_strategy           — istniejące entries → merge bez nadpisania (default)
  test_import_overwrite_strategy       — flag overwrite → nadpisuje

tests/test_ctx_detect.py:
  test_detect_python_project           — fake repo z setup.py → "python"
  test_detect_node_project             — package.json → "node"
  test_detect_git_root                 — od subdir → root .git
```

**Manual smoke:**
- prawym na pustej sesji Claude Code → Edit ctx → wizard się otwiera
- przejdź pełny wizard → check ~/.claude-context/<project>.db utworzona
- otwórz edytor ctx → dodaj key, edit, delete
- export do JSON → zaimportuj w nowym projekcie → spójność danych

**Kryterium zaliczenia:**
- [x] 5+3+3+4+3 = 18 nowych testów
- [x] 22 regresja
- [x] manual: wizard pełny flow + edytor + import/export round-trip

---

### Etap 8 — `bterminal/ui/panels/ctx_manager.py`

**Co przenosimy** (829 L):
`CtxManagerPanel` (god class — 25 metod).

**Po wcześniejszych ekstrakcjach Ctx (Etap 7) ten panel staje się głównie UI shim.**

**Regresja:**
- pełna pytest suite — Ctx panel był (lub będzie) dotykany przez exploration

**Nowe testy:**

```
tests/test_panel_ctx_manager.py:
  test_panel_imports                    — moduł importuje
  test_panel_instantiates               — Panel(fake_app)
  test_panel_lists_projects             — fake DB → projekty widoczne
  test_panel_select_project_loads_entries
                                        — kliknij projekt → entries renderowane
  test_panel_refresh_after_external_edit
                                        — entries dodane przez `ctx set` z CLI → refresh widzi
  test_panel_screenshot_baseline        — visual diff
```

**Manual smoke:**
- otwórz panel Ctx → projekty z disk widoczne
- wybierz projekt → entries renderują
- z CLI: `ctx set bterminal test_key test_value` → odśwież panel → key widoczny
- prawym na entry → Edit → zmiana persystuje

**Kryterium zaliczenia:**
- [x] 6 testów panel
- [x] 22 regresja
- [x] manual: external edit → refresh widzi

---

### Etap 9 — `bterminal/updater.py`

**Co przenosimy** (484 L):
`_load_local_errata`, `_check_for_updates`, `_do_update`, dialog aktualizacji + live log + progress bar.

**Regresja:**
- żaden istniejący test nie pokrywa updater (manualne)

**Nowe testy (`tests/test_updater.py`):**

```
test_load_local_errata_empty          — brak pliku errata → []
test_load_local_errata_chronological  — entries w errata → najnowsza pierwsza (index 0)
test_check_updates_offline            — fail fetch GitHub → graceful, no crash
test_check_updates_no_new_version     — current = latest → "up to date"
test_check_updates_new_version        — fake remote tag > current → dialog dostępny
test_update_dialog_strips_ansi        — log z ANSI escape codes → dialog renderuje plain text
test_update_progress_bar_advances     — fake install.sh emituje markery → progress bar fragmentaryczny
test_update_rollback_on_error         — install.sh fail → rollback dialog (regression: 85db1cb)
```

Mockujemy `urllib.request` i `subprocess.run` dla install.sh.

**Manual smoke:**
- `bterminal` (z auto-check) → po starcie dialog "available update" jeśli tag różny
- klik "Update" → live log scrolluje, progress bar postępuje
- celowo zepsuj install.sh → rollback dialog z user-friendly message
- sprawdź że errata pokazuje się od najnowszej

**Kryterium zaliczenia:**
- [x] 8 testów (wszystkie z mockami — bezpieczne)
- [x] 22 regresja
- [x] manual: full update cycle (na test branch z fake tag)

---

### Etap 10 — `bterminal/app.py` (BTerminalApp)

**Co przenosimy** (977 L):
`BTerminalApp` — orchestrator. Po wszystkich wcześniejszych ekstrakcjach klasa robi się *thin*: `__init__`, `run`, delegacja do paneli/managerów.

**Regresja:**
- `test_gtk_alongside_sidecars.py` — pełen boot
- WSZYSTKIE testy używają `bterminal_process` fixture która spawnuje BTerminalApp — więc każdy test = sanity check

**Nowe testy (`tests/test_app.py`):**

```
test_app_imports                       — `from bterminal.app import BTerminalApp`
test_app_construct                      — App() bez raise (z fake config dir)
test_app_window_created                 — App().window nie None, window.get_visible()
test_app_panels_registered              — wszystkie 8 wbudowanych paneli zarejestrowane w sidebar_stack
test_app_managers_initialized           — session_manager, claude_manager, consult_manager, sidecar_runner istnieją
test_app_clean_shutdown                 — quit() → child processes zabite, fd zwolnione
```

**Manual smoke:**
- `bterminal` → okno GUI
- przełącz między wszystkimi panelami w sidebar
- otwórz tab SSH + Claude → oba działają
- close window (X) → wszystkie subprocess zniknęły (`ps -ef | grep -E "claude|ssh|sidecar"`)

**Kryterium zaliczenia:**
- [x] 6 testów app
- [x] 22 regresja (krytyczne — app jest hub)
- [x] manual: clean shutdown bez sierot

---

### Etap 11 — `bterminal/__main__.py`

**Co przenosimy** (~50 L):
`main()` + argparse, sygnały, lock file (jeśli jest).

**Regresja:**
- każdy test używa `python bterminal.py` (przez fixture) — po Etapie 11 zmieniamy na `python -m bterminal`
- aktualizacja `tests/conftest.py` w tym samym commicie

**Nowe testy (`tests/test_entry_point.py`):**

```
test_python_m_bterminal                 — `python -m bterminal --help` → exit 0, usage text
test_legacy_bterminal_py_works          — bterminal.py jako thin shim też działa (back-compat dla install.sh)
test_argparse_debug_rest_flag           — `--debug-rest` → BTERMINAL_DEBUG_REST=1
test_argparse_version                   — `--version` → "BTerminal X.Y.Z"
```

**Manual smoke:**
- `python -m bterminal` → start identyczny jak przedtem
- `~/.local/bin/bterminal` (symlink) → start
- `bterminal --debug-rest` → marker widoczny

**Aktualizacja `install.sh`:**
- jeśli zmieniono ścieżkę entry point → fix symlink target
- test: świeża instalka na czystej VM

**Kryterium zaliczenia:**
- [x] 4 testy entry point
- [x] 22 regresja (po update conftest.py)
- [x] symlink z ~/.local/bin działa
- [x] **install.sh test na czystej VM** (osobny VM lub snapshot)

---

### Etap 12 — Cleanup

**Co robimy:**
- `bterminal.py` → thin shim albo `_bterminal_legacy.py` (decyzja: thin shim, żeby `~/.local/bin/bterminal` symlink dalej działał)
- aktualizacja `README.md` o nowej strukturze
- aktualizacja `CLAUDE.md` jeśli istnieje
- aktualizacja `install.sh` finalnych ścieżek

**Regresja:**
- pełna pytest suite (24 testów z slow)
- random-walk explorer 1000 kroków (`test_exploration.py`)
- action-graph wszystkie scenariusze

**Nowe testy:**
- `tests/test_legacy_shim.py` — `bterminal.py` jako entry point dalej startuje aplikację

**Manual smoke (NA CZYSTEJ VM — instalator!):**
1. snapshot VM
2. clone repo
3. `./install.sh`
4. start `bterminal`
5. otwórz tab SSH, Claude, sidecar
6. zamknij wszystko clean
7. `bterminal --version`
8. `pytest -m "not slow"` na świeżej VM

**Kryterium zaliczenia:**
- [x] full suite green (24/24)
- [x] random-walk 1000 kroków bez resource leak
- [x] action-graph wszystkie scenariusze
- [x] **clean install na świeżej VM przechodzi**
- [x] manual smoke pełna ścieżka

---

## 2. Master regression checklist (po każdym etapie)

Przed `git commit` etapu N — uruchom:

```bash
# 1. Import sanity
python -c "import bterminal; print('OK')"

# 2. Pełna pytest (bez slow)
pytest -v -m "not slow" 2>&1 | tail -3
# Oczekiwane: "X passed, Y skipped"

# 3. Smoke startu (xvfb)
xvfb-run -a timeout 5 python -m bterminal --debug-rest &
sleep 2
curl -sf -H "X-Debug-Token: $(cat ~/.config/bterminal/debug_token)" http://127.0.0.1:7780/api/health
curl -sf -X POST -H "X-Debug-Token: $(cat ~/.config/bterminal/debug_token)" "http://127.0.0.1:7780/api/quit?confirm=true"

# 4. Visual diff (opcjonalnie)
# screenshot baseline vs po-refactor — md5 lub PIL diff
```

**Jeśli któryś krok pada — STOP, debug, fix przed commitem.**

---

## 3. Slow E2E suite (przed mergiem etapu)

```bash
# Wymaga: agent_controller cwd, free port 8766, system deps
pytest -v -m slow 2>&1 | tail -5

# Action graph pełen scenariusz:
pytest tests/test_action_graph.py -v

# Random walk explorer:
pytest tests/test_exploration.py -v --explore-steps=1000
```

---

## 4. Visual regression infrastructure (TODO przed Etapem 5)

Etap 5 (panele) wymaga porównania screenshotów. Proponuję dodać przed Etapem 5:

```
tests/visual/
├── __init__.py
├── conftest.py          # screenshot fixture: REST GET /api/window/screenshot, save w tmp
├── baseline/            # commitowane PNG-i bazowe
│   ├── consult.png
│   ├── tasks.png
│   └── ...
└── compare.py           # PIL diff: < 1% pikseli różnych = pass
```

Każdy `test_panel_<X>_screenshot_baseline` używa tego frameworka.

**Decyzja:** robimy to przed Etapem 5 czy używamy md5 (binarne porównanie, bez tolerancji)?

---

## 5. Test stage gate — kryteria pomiędzy etapami

**Po każdym etapie** *przed* przejściem do następnego:

| Gate | Wymóg |
|------|-------|
| Import sanity | `import bterminal` bez błędu |
| Unit testy modułu | wszystkie nowe testy green |
| Regresja | pytest `-m "not slow"` 22/22 |
| Manual smoke | operator przeszedł listę dla danego etapu |
| Screenshot diff | brak nieoczekiwanych zmian wizualnych |
| `bterminal.py` startuje | `~/.local/bin/bterminal` dalej action |

**Brak punktu = brak commitu etapu, brak przejścia dalej.**

---

## 6. Plan B — gdyby się rozjechało

Jeśli któryś etap pęknie głębiej niż przewidziano:

1. **Rollback brancha:** `git reset --hard HEAD~1` — etap jest osobnym commitem, zawsze odwracalny
2. **Rerun:** sprawdź co konkretnie padło, popraw lokalnie, retry
3. **Skip:** w skrajnym przypadku oznacz etap "DEFERRED", przejdź do kolejnego (np. updater można odłożyć — nie ma zależności)

**Zasada:** nigdy nie commitujemy zepsutego etapu. Lepiej `git reset` niż "fixup w następnym commicie".

---

## 7. Stan testów po Etapie 12 (oczekiwany)

| Kategoria | Liczba | Czas wykonania |
|-----------|--------|---------------|
| Unit (config, models, ctx, dialogs) | ~50 | < 5s |
| Smoke panele (8) | ~40 | < 30s (xvfb) |
| Tab + sidebar | ~10 | < 15s |
| Plugin contracts | 7 | < 5s |
| Debug REST suite | 22 (istniejące) | < 10s |
| Updater (mocked) | 8 | < 5s |
| Slow E2E | 2 | ~30s |
| Action graph | per-scenariusz | varied |
| **TOTAL "not slow"** | **~140** | **< 60s** |
| **TOTAL pełen** | **~160** | **< 5 min** |

Bazowo mamy 24 testy, docelowo ~140-160 — **6-7x wzrost coverage** w trakcie refaktoryzacji.

---

## 8. Co testujemy poza pytest

### Visual
- screenshot diff per panel (Etap 5)
- screenshot diff CSS (Etap 1)
- czerwony marker debug-rest (Etap 3 manual)

### Manual checklist (operator)
- każdy etap ma listę "manual smoke" — operator klika
- na koniec (Etap 12) — clean install na świeżej VM

### Performance / leak
- random-walk 1000 kroków (`test_exploration.py`) — sanity że nie ma resource leak po refactorze
- przed commitem ostatniego etapu: `valgrind --tool=massif` (opcjonalnie) na hot path

---

**Następny krok:** akceptacja planu, potem Etap 1 — `bterminal/config.py` + `tests/test_config.py`.
