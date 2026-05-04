# BTerminal — refaktoryzacja monolitu na strukturę modułową

**Plik źródłowy:** `bterminal.py` — **11 758 linii**
**Branch:** `feat/sidecar-plugins`
**Data analizy:** 2026-05-04
**Cel:** rozbić jeden plik na ~25 modułów po średnio ~470 linii bez psucia działającej aplikacji.

---

## 1. Mapa zawartości — top-level definicje

### Sekcja: stałe i konfiguracja (L38–56)
- `_read_version()` — wersja aplikacji

### Sekcja: Debug REST API (L58–893) — **836 L**
- `_generate_debug_token()`, `_load_or_create_debug_token()`
- `_rotate_debug_log_if_needed()`, `_audit_log()`
- `BTerminalDebugHandler` (L141, 81 L) — HTTP request handler
- `BTerminalDebugServer` (L222, 13 L) — HTTPServer subclass
- `_via_glib_idle()` (L235, 25 L) — most GTK ↔ wątek REST
- `_route_*()` (L260–854) — 26 handlerów GET/POST/PUT
- `_start_debug_rest_server()`, `_stop_debug_rest_server()`, `_start_idle_watchdog()`

### Sekcja: sidecary (L895–1223) — **247 L**
- `SidecarManifest` (L898) — `@dataclass`
- `SidecarDiscovery` (L929) — loader manifestów
- `SidecarRunner` (L958) — subprocess manager
- `HealthChecker` (L1026) — pinger `/health`

### Sekcja: config & utility (L1223–1540) — **317 L**
- `_load_options`, `_save_options`, `_find_claude_path`, `_claude_log_dir`
- `_build_css`, `_parse_color`, `_session_color`
- `show_error_dialog`, `show_info_dialog`

### Sekcja: data models (L1540–1755) — **217 L**
- `JsonListManager` (L1540, 65 L)
- `SessionManager` (L1605, 11 L)
- `ClaudeSessionManager` (L1616, 10 L)
- `ConsultManager` (L1626, 131 L)

### Sekcja: dialogs & ctx (L1757–3088) — **1 407 L**
- `SessionDialog` (L1757, 95 L)
- `MacroDialog` (L1918, 153 L)
- `ClaudeCodeDialog` (L2178, 223 L)
- `CtxSetupWizard` (L2638, 329 L) — duża, 10 metod
- `_CtxEntryDialog`, `_CtxProjectDialog`, `CtxEditDialog`

### Sekcja: usage stats (L3236–3430) — **296 L**
- `_SessionStatsReader`, `SessionStatsBar`

### Sekcja: UI components (L3546–10186)
- **`TerminalTab`** (L3546, 765 L, 20 metod) — *god class*
- **`SessionSidebar`** (L4311, 798 L, 30 metod) — *god class*
- `_CtxExportDialog`, `_CtxImportDialog`
- **`CtxManagerPanel`** (L5645, 829 L, 25 metod) — *god class*
- `ConsultPanel` (L6474, 761 L)
- `TaskListPanel` (L7278, 512 L)
- `GitPanel` (L7790, 618 L)
- `MemoryPanel` (L8455, 568 L)
- `SkillsPanel` (L9023, 339 L)
- `FilesPanel` (L9362, 510 L)
- `PluginManagerPanel` (L9872, 315 L)
- `OptionsDialog` (L10187, 111 L)

### Sekcja: core app (L10298–11758) — **1 461 L**
- **`BTerminalApp`** (L10298, 977 L, 31 metod) — *core orchestrator*
- Updater: `_load_local_errata`, `_check_for_updates`, `_do_update` — 484 L
- `main()` (L11709, 50 L)

---

## 2. Sekcje semantyczne — naturalne granice

Plik ma 18 sekcji oznaczonych komentarzami `# ─── … ──`. Każda jest kandydatem na osobny moduł:

| Linia | Sekcja |
|-------|--------|
| L38 | Stałe i konfiguracja |
| L58 | Debug REST API |
| L895 | Sidecar infrastructure |
| L1537 | SessionManager |
| L1623 | ConsultManager |
| L1754 | SessionDialog |
| L1849 | MacroDialog |
| L2068 | ClaudeCodeDialog |
| L2393 | CtxEditDialog |
| L3236 | Claude usage cache |
| L3338 | SessionStatsBar |
| L3543 | TerminalTab |
| L4300 | SessionSidebar |
| L5106 | Ctx Import/Export |
| L5642 | CtxManagerPanel |
| L6471 | ConsultPanel |
| L7232 | TaskListPanel |
| L7787 | GitPanel |
| L8405 | BTerminalApp |
| L11273 | main |

---

## 3. Grupowanie logiczne — 15 modułów kandydujących

| Grupa | Nazwa modułu | Linii | Status | Zewnętrzne zależności |
|-------|-------------|-------|--------|----------------------|
| A | Debug REST | 836 | gotowe do ekstrakcji | `http.server`, `threading`, GTK |
| B | Sidecary | 247 | gotowe | `subprocess`, `json`, `urllib` |
| C | Config & Theme | 317 | gotowe | GTK, `json`, `os` |
| D | Data Models | 217 | gotowe | `json`, `pathlib` |
| E1–E7 | Dialogs & Ctx | 1 407 | rozbić na 7 plików | GTK, `sqlite3`, `subprocess` |
| F | Stats | 296 | gotowe | GTK, `threading`, `urllib` |
| G1 | TerminalTab | 765 | duża — później rozbić | VTE, GTK, `subprocess` |
| G2 | SessionSidebar | 798 | duża — później rozbić | GTK, models |
| H | CtxManagerPanel | 829 | duża — później rozbić | GTK, `sqlite3`, git |
| I | ConsultPanel | 761 | gotowe | GTK, `subprocess` |
| J | TaskListPanel | 512 | gotowe | GTK, `sqlite3` |
| K | GitPanel | 618 | gotowe | GTK, `subprocess` |
| L1–L6 | Built-in panels | 1 890 | rozbić na 6 plików | GTK, `json` |
| M | BTerminalApp | 977 | thin orchestrator | wszystkie |
| N | Updater | 484 | gotowe | GTK, `urllib`, `json` |

**Razem:** ~11 758 L → ~25 plików, średnio ~470 L/plik.

---

## 4. Problematyczne miejsca

### 4.1 God classes (>500 linii)

| Klasa | Linii | Metod | Problem | Strategia |
|-------|-------|-------|---------|-----------|
| `BTerminalApp` | 977 | 31 | hub orkiestrujący, deleguje do paneli | thin orchestrator, deleguj do nowych modułów |
| `TerminalTab` | 765 | 20 | VTE + Claude session state w jednym | rozbić na `Tab` (UI) + `SessionState` (logic) |
| `SessionSidebar` | 798 | 30 | listing + edytor + UI | `SessionList` + `SessionEditor` |
| `CtxManagerPanel` | 829 | 25 | file browser + git + metadata | `CtxBrowser` + `GitBridge` |
| `ConsultPanel` | 761 | 18 | OK — logika + UI mocno spojone, mało zależności | zostawić w jednym pliku |

### 4.2 Cykle zależności

**Brak.** Architektura liniowa:

```
main() → BTerminalApp → (panels, managers, sidecary) → (GTK, sqlite, json, threading)
```

Implikacja: ekstrakcje można robić w **dowolnej kolejności**.

### 4.3 Globalne stany

| Symbol | Problem | Cel |
|--------|---------|-----|
| `_OPTIONS` | dict zamiast singletona | `Config` class w `config.py` |
| `CATPPUCCIN`, `TERMINAL_PALETTE`, `CSS` | rozrzucone po globals | konsolidacja w `theme.py` |
| `REPO_DIR` | nieznane źródło — sprawdzić | zamodelować |

### 4.4 GTK callback splątanie

**Brak ryzyka** — callback'i już są izolowane w metodach klas, można przenosić bez problemów.

---

## 5. Docelowa struktura katalogów

```
bterminal/
├── __init__.py
├── __main__.py              # entry point — main()
├── app.py                   # BTerminalApp (thin orchestrator)
├── config.py                # _load_options, paths, OPTIONS, Catppuccin, CSS, _build_css
├── models.py                # SessionManager, ConsultManager, JsonListManager, ClaudeSessionManager
├── updater.py               # auto-update + errata
├── debug_rest.py            # debug REST: server + handler + 26 route handlers
│
├── plugins/                 # plugin SYSTEM (loadery + runtime; nie same pluginy)
│   ├── __init__.py          # BTerminalPlugin ABC (kontrakt in-process)
│   ├── inproc_loader.py     # importlib loader dla ~/.config/bterminal/plugins/
│   └── sidecar/
│       ├── __init__.py
│       ├── manifest.py      # SidecarManifest (@dataclass)
│       ├── discovery.py     # SidecarDiscovery
│       ├── runner.py        # SidecarRunner (subprocess)
│       └── health.py        # HealthChecker
│
├── ctx/                     # Ctx domain (własny obszar)
│   ├── __init__.py
│   ├── wizard.py            # CtxSetupWizard
│   ├── editor.py            # CtxEditDialog + _CtxEntryDialog + _CtxProjectDialog
│   ├── import_export.py     # _CtxImportDialog, _CtxExportDialog
│   ├── detect.py            # project root detection
│   └── images.py            # image management
│
└── ui/
    ├── __init__.py
    ├── terminal_tab.py      # TerminalTab (765 L)
    ├── sidebar.py           # SessionSidebar (798 L)
    ├── stats.py             # SessionStatsBar + _SessionStatsReader
    ├── helpers.py           # show_error_dialog, show_info_dialog, ShrinkableBin
    ├── dialogs/
    │   ├── __init__.py
    │   ├── claude_code.py   # ClaudeCodeDialog
    │   ├── sessions.py      # SessionDialog, MacroDialog
    │   └── options.py       # OptionsDialog
    └── panels/              # built-in panele (NIE są pluginami)
        ├── __init__.py      # bazowe utility, jeśli wyjdą wspólne
        ├── ctx_manager.py   # CtxManagerPanel (829 L)
        ├── consult.py       # ConsultPanel (761 L)
        ├── tasks.py         # TaskListPanel (512 L)
        ├── git.py           # GitPanel (618 L)
        ├── memory.py        # MemoryPanel (568 L)
        ├── skills.py        # SkillsPanel (339 L)
        ├── files.py         # FilesPanel (510 L)
        └── plugin_manager.py # PluginManagerPanel — UI do zarządzania pluginami

tests/                        # już istnieje
docs/                         # już istnieje
examples/sidecars/            # referencyjne manifesty sidecarów
```

**Uzasadnienie kluczowych decyzji:**

1. **`plugins/` = infrastruktura, nie implementacje** — same pluginy żyją w innych repach (RemoteControll, agent_controller/plugins, agent-tester). W BTerminalu trzymamy tylko *kontrakty* i *loadery*.

2. **`plugins/sidecar/` jako sub-pakiet** — sidecar to 4 skoordynowane komponenty (Manifest, Discovery, Runner, Health). Trzymanie ich razem oddzielnie od starego kontraktu `BTerminalPlugin` daje jasną granicę "stary kontrakt vs nowy".

3. **`PluginManagerPanel` w `ui/panels/`, nie w `plugins/`** — to JEST panel UI, tylko *temat* ma plugin. Runtime jest w `plugins/`, UI do zarządzania nim w `ui/panels/`.

4. **`ctx/` na poziomie pakietu (nie pod `ui/`)** — Ctx ma własną domenę: schemat DB, wizard flow, edytor, import/export, detection. Pod `ui/` sugerowałoby że to tylko UI.

5. **Theme w `config.py` (nie osobny `theme.py`)** — prościej, mniej plików; Catppuccin + CSS + `_build_css` siedzą obok `_load_options` bo obie warstwy to "konfiguracja prezentacji".

6. **`ui/panels/`** — wszystkie panele to GTK widgety, naturalnie pasują pod `ui/`. Prefix `ui.panels.consult` jest jasny.

7. **`debug_rest.py` jednoplikowo** — 836 L, 26 handlerów ale prosty wzorzec (regex router → handler). Rozbicie na `api/routes/{tabs,window,sidecars,...}` możliwe później jeśli urośnie. Na razie YAGNI.

---

## 6. Plan migracji — 12 etapów

Każdy etap zostawia działający program. Każdy etap = osobny commit. **Walidacja po każdym** etapie przed przejściem dalej.

### Etap 1 — Config + theme (317 L) → `bterminal/config.py`
- przenieś `_load_options`, `_save_options`, ścieżki, `OPTIONS`, `CATPPUCCIN`, `TERMINAL_PALETTE`, `CSS`, `_build_css`, `_parse_color`, `_session_color`
- w `bterminal.py` zostaw `from bterminal.config import …`
- **test:** GUI bez zmian, CSS aplikuje
- **commit:** `refactor: extract config module`

### Etap 2 — Models (217 L) → `bterminal/models.py`
- `JsonListManager`, `SessionManager`, `ClaudeSessionManager`, `ConsultManager`
- **test:** dodaj sesję SSH, zmień opcje
- **commit:** `refactor: extract data models`

### Etap 3 — Debug REST (836 L) → `bterminal/debug_rest.py`
- `BTerminalDebugHandler`, `BTerminalDebugServer`, wszystkie `_route_*`, watchdog, helpers
- **test:** `bterminal --debug-rest`, `curl /api/health`, smoke testy z `tests/`
- **commit:** `refactor: extract debug REST API`

### Etap 4 — Plugin runtime (247 L + kontrakt) → `bterminal/plugins/`
- `bterminal/plugins/__init__.py` — `BTerminalPlugin` ABC (kontrakt in-process)
- `bterminal/plugins/inproc_loader.py` — `_load_plugins` (importlib loader)
- `bterminal/plugins/sidecar/manifest.py` — `SidecarManifest`
- `bterminal/plugins/sidecar/discovery.py` — `SidecarDiscovery`
- `bterminal/plugins/sidecar/runner.py` — `SidecarRunner`
- `bterminal/plugins/sidecar/health.py` — `HealthChecker`
- **test:** discovery manifestów, lifecycle (`/api/sidecars/*/start`), in-process plugin (RemoteControll) ładuje się
- **commit:** `refactor: extract plugin runtime (in-process + sidecar)`

### Etap 5 — UI panels (1 890 L) — 8 commitów
Po jednym pliku na panel: `stats` (do `ui/stats.py`), `memory`, `skills`, `files`, `tasks`, `git`, `consult`, `plugin_manager` (wszystkie do `ui/panels/`).
- **test:** każdy panel otwiera się, podstawowe akcje działają
- **commits:** `refactor: extract {panel_name}`

### Etap 6 — Sidebar + TerminalTab (1 563 L) — 2 commity
- `bterminal/ui/sidebar.py` (`SessionSidebar`)
- `bterminal/ui/terminal_tab.py` (`TerminalTab`)
- **test:** otwórz tab SSH, otwórz tab Claude Code, zamknij, edytuj sesję
- **commits:** `refactor: extract SessionSidebar`, `refactor: extract TerminalTab`

### Etap 7 — Dialogs & Ctx (1 518 L) — 7 commitów
- `bterminal/ui/dialogs/sessions.py` — `SessionDialog`, `MacroDialog`
- `bterminal/ui/dialogs/claude_code.py` — `ClaudeCodeDialog`
- `bterminal/ui/dialogs/options.py` — `OptionsDialog` (111 L)
- `bterminal/ctx/wizard.py`, `editor.py`, `import_export.py`, `detect.py`, `images.py`
- **test:** dialog edycji sesji, wizard ctx, import/export, options
- **commits:** `refactor: extract {ctx_module}`

### Etap 8 — `CtxManagerPanel` (829 L) → `bterminal/ui/panels/ctx_manager.py`
- duża klasa, ale po wcześniejszych ekstrakcjach Ctx już sporo zostanie odsłonięte
- **test:** otwórz panel Ctx, edytuj entry
- **commit:** `refactor: extract CtxManagerPanel`

### Etap 9 — Updater (484 L) → `bterminal/updater.py`
- `_check_for_updates`, `_do_update`, `_load_local_errata`, dialog aktualizacji
- **test:** ręczny check for updates
- **commit:** `refactor: extract updater`

### Etap 10 — `BTerminalApp` (977 L) → `bterminal/app.py`
- po przeniesieniu wszystkiego innego ta klasa staje się thin orchestratorem
- **test:** pełen boot aplikacji
- **commit:** `refactor: move BTerminalApp to module`

### Etap 11 — Entry point → `bterminal/__main__.py`
- `main()` + parsowanie argparse
- **test:** `python -m bterminal` i `~/.local/bin/bterminal` dalej działają
- **commit:** `refactor: extract main entry point`

### Etap 12 — Cleanup
- `bterminal.py` zostaje cienki shim (`from bterminal.__main__ import main; main()`) lub jest archiwizowany
- aktualizacja `install.sh` jeśli ścieżki się zmieniły
- **test:** świeża instalacja na czystej VM
- **commit:** `refactor: archive legacy monolithic file`

### Etap 13 (opcjonalny, później) — rozbicie god classes
- `TerminalTab` → `Tab` + `SessionState`
- `SessionSidebar` → `SessionList` + `SessionEditor`
- `CtxManagerPanel` → `CtxBrowser` + `GitBridge`

---

## 7. Walidacja po każdym etapie

| Etap | Test |
|------|------|
| 1 | GUI bez zmian, CSS aplikuje |
| 2 | dodaj sesję SSH, zmień opcje |
| 3 | `bterminal --debug-rest`, `curl /api/health`, pytest smoke |
| 4 | manifest → discovery → logi |
| 5 | każdy panel otwiera się |
| 6 | tab lifecycle (otwórz/zamknij SSH i Claude) |
| 7 | dialogi otwierają, edytują |
| 8 | panel Ctx pełny workflow |
| 9 | `--check-for-updates` |
| 10 | full boot |
| 11 | `python -m bterminal` |
| 12 | clean install na VM |

---

## 8. Ryzyka i mitygacje

| Ryzyko | Mitygacja |
|--------|-----------|
| circular imports | dependency injection — `app` jako parametr przekazywany |
| REST router potrzebuje `BTerminalApp` | resolve przy starcie, attach jako `server.app` (już tak jest) |
| globalne stałe rozproszone | konsolidacja w Etapie 1 |
| `install.sh` polega na ścieżkach | aktualizacja w Etapie 12 |
| zależność testów od `bterminal.py` | adapter w `conftest.py` po Etapie 11 |

---

## 9. Zyski

- **Testowalność:** każdy moduł niezależnie, mocking dużo prostszy
- **Czytelność:** ~470-linijkowy plik vs 11 758
- **Maintainability:** nowe features w dedykowanych modułach
- **Reuse:** `ctx`, `sidecars`, `debug_rest` można importować w innych projektach
- **Bezpieczeństwo:** brak cykli, callback'i izolowane — refaktor niskiego ryzyka

---

## 10. Podsumowanie liczb

- **Plik źródłowy:** 11 758 linii
- **Docelowa liczba modułów:** ~25
- **Średnia wielkość modułu:** ~470 linii
- **Liczba etapów:** 12 (+ 1 opcjonalny)
- **Liczba commitów:** ~24 (część etapów = wiele commitów)
- **Cykle zależności:** 0
- **God classes do rozbicia później:** 4

**Następny krok:** Etap 1 — `bterminal/config.py`. Branch roboczy proponuję `feat/modular-refactor` rozgałęziony z `feat/sidecar-plugins`.
