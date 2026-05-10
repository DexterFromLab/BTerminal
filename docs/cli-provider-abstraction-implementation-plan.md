# Plan implementacji: abstrakcja CLI providera (Claude + Copilot)

**Data:** 2026-05-06
**Powiązane:** `docs/cli-provider-abstraction-analysis.md` (analiza), `docs/REQUIREMENTS.md` R4/R4a/R4b/R7a.
**Kontekst:** użytkownik nie posiada aktualnie subskrypcji GitHub Copilot. Plan zaprojektowany tak, by **maksymalnie wszystko zrealizować i przetestować mockiem**, odkładając tylko 3 punkty wymagające realnej weryfikacji z żywym `copilot` CLI.

---

## Status implementacji — 2026-05-07

**Implemented:** Tier 1 (T1.1–T1.11) ✅ · Tier 2 (T2.1–T2.12) ✅ · Tier 3 (T3.1–T3.10) ✅ · Tier 4 — T4.1 ✅ · T4.2 ✅ · T4.3 ✅ · T4.4 ✅ · T4.5 ✅ · T4.6 ⏳ deferred do T4.6.1 (po V1+V2 + 1 release) · T4.7 ✅ (ten dokument + README + analysis doc) · **T4.8 open**.

**Workflow infra (poza pierwotnym planem):** V1 (updater bug fix) ⏳ · V2 (host rsync sync) ⏳ · V3 (VM test runner) ✅ — `tools/vm_{sync,test,install}.sh`.

**Live verification (L1-L3):** ⏸ blocked: czeka na subskrypcję Copilot.

**Test suite po Tier 4 baseline:** **829 passed**, run-time fast suite ~13 s (host-side), pełna --slow ~30 s na VM.

Szczegóły w `docs/cli-provider-abstraction-analysis.md` § 12 "Status implementacji — Implemented".

---

## 1. Zasada przewodnia: "Mock-first, live-later"

**Co robimy bez subskrypcji (~95 % planu):**
- Cała architektura: `bterminal/providers/`, registry, capabilities matrix, dispatch.
- Refactor: `AISessionManager`, migracja `claude_sessions.json`, dialog z dropdownem.
- `CopilotProvider.build_argv()` — tylko buduje argv stringa, nie odpala go.
- Parser `events.jsonl` dla Copilota → na podstawie próbek z dokumentacji + community blog posts.
- Mock CLI (`tools/mock_ai_cli`) — rozszerzenie o scenariusz `copilot_basic.json` (emituje fake `events.jsonl` w stylu Copilota).
- Wszystkie testy (unit + component + E2E) — używają mocka, nie żywego Copilota.

**Co odkładamy do momentu zakupu subskrypcji (3 follow-up tasks):**
- **L1 (live-1):** smoke test, że argv `copilot --no-banner --no-mouse --plain-diff -p "..."` rzeczywiście odpala bez błędów i agent odpowiada.
- **L2:** weryfikacja, że tail-f `events.jsonl` widzi te eventy które przewidujemy (`tool.execution_complete`, `session.shutdown`).
- **L3:** weryfikacja, że pole `session.shutdown.modelMetrics.*.requests.cost` ma format jaki zakładamy (USD float vs PRU integer).

Te 3 punkty zostają jako **post-MVP follow-up**, oznaczone w kodzie komentarzami `# TODO(L1/L2/L3): verify with live Copilot`.

---

## 2. Strategia testowa

### 2.1 Layer pyramid (po zmianach)

```
E2E (slow)        ████  ~5 nowych testów (provider switching, migration)
Component (mid)   ████████████  ~12 nowych testów (spawn, dialog, REST, stats)
Unit (fast)       ████████████████████  ~20 nowych testów (registry, capabilities, parsers)
                                        ─────────
Total nowych: ~37 testów + 207 obecnych = ~244
Time budget:  pełna regresja < 35 s (z 26 s obecnie)
```

### 2.2 Mock CLI rozszerzenie

**Obecny stan:** `tools/mock_ai_cli` jest provider-agnostic state machine. Scenariusze: tylko `tests/scenarios/claude_basic.json`.

**Zmiana:** dodaj scenariusze:
- `tests/scenarios/copilot_basic.json` — odpowiada w stylu Copilota (TUI-safe output).
- `tests/scenarios/copilot_events_jsonl.json` — emituje na stdout sekwencję eventów w formacie `events.jsonl` (typ `session.start`, `tool.execution_start`, `tool.execution_complete`, `session.shutdown` z `modelMetrics`).
- `tests/scenarios/copilot_idle.json` — pauza między eventami żeby testować idle detection.

**Mock CLI capability extension:** dodaj nową dyrektywę `emit_events_jsonl` w scenario JSON — mock pisze JSONL eventy w `~/.copilot/session-state/<uuid>/events.jsonl` zamiast tylko stdout. Pozwala testować `CopilotStatsReader` bez żywego CLI.

### 2.3 Test commands

```bash
# Unit + component (fast, dla pre-commit)
./tools/test_all.sh --quick                 # 0.3 s, jednostki
./tools/test_all.sh                         # 16 s, fast suite

# Pełna regresja przed PR
./tools/test_all.sh --slow                  # 35 s (oczekiwane po zmianach)

# Tylko provider abstraction
pytest tests/test_providers.py -v
pytest tests/test_ai_session_manager.py -v
pytest tests/test_copilot_stats_reader.py -v

# E2E provider switching
pytest tests/e2e/test_provider_switching.py -v
```

### 2.4 Definition of Done per zadanie

Każde zadanie (T-#) jest "done" gdy:
1. Kod napisany.
2. Co najmniej 1 unit test napisany i zielony.
3. Test suite (`./tools/test_all.sh`) zielony bez regresji.
4. Jeśli zadanie dotyka UI: smoke test ręczny (otwórz/zamknij/kliknij — bez crashu) udokumentowany w komentarzu PR.
5. Plik `docs/test-coverage-matrix.md` aktualizowany — nowe testy zmappowane do R-numerów.

---

## 3. Tier 1 — Foundation (~15 dni)

**Cel:** zbudować warstwę abstrakcji, ZERO zmian w UX dla użytkownika końcowego. Test suite zielony.

### T1.1 — Utwórz `bterminal/providers/base.py` (1 dzień)
- **Pliki:** `bterminal/providers/__init__.py`, `bterminal/providers/base.py`
- **Zawartość:** `@dataclass ProviderCapabilities`, `@dataclass ProviderDisplay`, `class AIProvider(ABC)` z metodami:
  - `find_binary() -> str | None`
  - `build_argv(config, intro_prompt) -> list[str]`
  - `session_log_glob(project_dir) -> str | None`
  - `parse_session_stats(log_path) -> SessionStats`
  - `fetch_plan_usage() -> dict | None` (default: None)
  - `detect_idle(...) -> bool` (default: timeout-based)
- **Test:** `tests/test_providers_base.py::test_capabilities_defaults` — instancja z minimalnymi wymaganymi polami nie crashuje.

### T1.2 — Provider config loader (1 dzień)
- **Pliki:** `bterminal/providers/__init__.py`, `bterminal/providers/defaults.json`
- **Zawartość:** `defaults.json` z pełnym schema dla `claude` (zgodne z obecnym behawiorem) i `copilot` (z capabilities w większości false initially). Loader: `load_providers_config(user_path: Path | None) -> dict` — merge defaults + user override (deep merge).
- **Test:** `test_providers_config_loader.py::test_user_override_wins`, `test_missing_user_falls_back_to_defaults`.

### T1.3 — `ClaudeProvider` implementation (2 dni)
- **Pliki:** `bterminal/providers/claude.py`
- **Zawartość:** subklasa `AIProvider`. Wszystkie metody implementowane 1:1 z obecnym kodem (`_find_claude_path`, current `build_argv` z `--resume`/`--dangerously-skip-permissions`, parsing `~/.claude/projects/.../*.jsonl`, fetch z `oauth/usage`).
- **Test:** `tests/test_claude_provider.py` — 5 testów (find_binary mock, argv builder z różnymi configami, session log glob, capability flags poprawne).

### T1.4 — `CopilotProvider` skeleton (1 dzień)
- **Pliki:** `bterminal/providers/copilot.py`
- **Zawartość:** subklasa z większością capabilities = false (kompletna implementacja w T2.3, T3.3). Tylko display + binary search paths zaimplementowane.
- **Test:** `tests/test_copilot_provider.py::test_capabilities_disabled_by_default`.

### T1.5 — `ProviderRegistry` (1 dzień)
- **Pliki:** `bterminal/providers/__init__.py`
- **Zawartość:** `class ProviderRegistry` — singleton, `register()`, `get(name)`, `all()`, `default_provider()`. Auto-load przy starcie BTerminal.
- **Test:** `test_provider_registry.py::test_register_and_get`, `test_default_provider`, `test_unknown_provider_raises`.

### T1.6 — Rename `ClaudeSessionManager` → `AISessionManager` (1 dzień)
- **Pliki:** `bterminal/models.py`, `bterminal/app.py`, `bterminal/ui/dialogs/sessions.py`, ~5 innych call-sitów.
- **Zawartość:** klasa zachowuje 100 % API. Plik na dysku NADAL `claude_sessions.json` w T1.6 (rename pliku w T1.7). Dodaj pole `provider` do schema z domyślną wartością `"claude"` jeśli brak.
- **Backward-compat shim:** `ClaudeSessionManager = AISessionManager` re-export w `models.py` na 1 release.
- **Test:** istniejące testy `test_models.py::test_claude_session_*` muszą przejść bez zmian.

### T1.7 — Migracja `claude_sessions.json` → `ai_sessions.json` (R4b) (2 dni)
- **Pliki:** `bterminal/models.py` (`_migrate_claude_to_ai_sessions`), wywołanie w `BTerminalApp.__init__`.
- **Zawartość:** funkcja idempotent. Czyta stary, pisze nowy z polem `provider="claude"`, opakowuje resume/skip_permissions/sudo w `provider_options`, robi backup `.bak`. Loguje na stderr.
- **Test:** `tests/test_migration_ai_sessions.py` — 4 testy:
  1. Brak pliku → no-op.
  2. Tylko stary plik → migracja, backup utworzony.
  3. Oba pliki → idempotent (no-op, bez nadpisania).
  4. Stary z `resume=true` → po migracji `provider_options.resume=true`.

### T1.8 — Update call-sitów `claude_config` → `ai_config` (2 dni)
- **Pliki:** `bterminal/app.py`, `bterminal/ui/terminal_tab.py`, `bterminal/debug_rest.py`, `bterminal/helpers.py`, `bterminal/ui/stats.py`, `bterminal/ui/dialogs/claude_code.py`.
- **Zawartość:** zmienna `tab.claude_config` → `tab.ai_config`. Każde użycie odczytuje `ai_config["provider"]` i odwołuje się do `registry.get(provider)` przed zachowaniem.
- **Backward-compat shim:** property `tab.claude_config` zwraca `tab.ai_config` jeśli `provider == "claude"`, else None.
- **Test:** istniejące E2E testy `test_smoke_*.py` muszą przejść bez zmian.

### T1.9 — Update `helpers._compute_intro_prompt_for_tab` (1 dzień)
- **Pliki:** `bterminal/helpers.py`, `bterminal/ui/dialogs/claude_code.py::_build_intro_prompt`
- **Zawartość:** intro prompt builder używa `tab.ai_config["provider"]`. Header: `f"You are working inside BTerminal — an SSH/{provider.display.long_label} terminal..."`. Reszta promptu (ctx, tools, rules, plugins) BEZ ZMIAN.
- **Test:** `test_intro_prompt_per_provider.py::test_header_uses_provider_long_label`.

### T1.10 — Test suite green + Tier 1 acceptance (1 dzień)
- **Komenda:** `./tools/test_all.sh --slow` zielony.
- **Smoke ręczny:** otwórz BTerminal, otwórz Claude Code session, zweryfikuj że wszystko działa identycznie jak przed Tier 1.
- **Sprawdź:** `~/.config/bterminal/ai_sessions.json` istnieje, `claude_sessions.json.bak` istnieje, sesje mają pole `provider="claude"`.

### T1.11 — Update `docs/test-coverage-matrix.md` po Tier 1 (0.5 dnia)
- Dodaj wpisy `R4 → tests/test_migration_ai_sessions.py`, `R4a → tests/test_provider_registry.py`, etc.

**Kryterium akceptacji Tier 1:**
- [ ] 207 obecnych + 12 nowych testów = 219 zielonych
- [ ] `~/.config/bterminal/ai_sessions.json` po migracji istnieje
- [ ] BTerminal otwiera Claude Code identycznie jak przed
- [ ] Test `git diff --stat HEAD~5` pokazuje tylko nowe pliki w `bterminal/providers/` + 7 zmienionych w innych modułach

---

## 4. Tier 2 — Spawn + Dialog (~25 dni)

**Cel:** użytkownik może wybrać Copilot w dialogu, otworzyć tab Copilota (wizualnie z ikoną 🤖), z mockiem CLI wszystko działa E2E.

### T2.1 — Refactor `spawn_claude` → `spawn_ai_cli` (2 dni)
- **Pliki:** `bterminal/ui/terminal_tab.py:189-284`
- **Zawartość:** metoda przyjmuje `config` z `provider` field. Dispatch: `provider = registry.get(config["provider"])`, `argv = provider.build_argv(config, intro_prompt)`, `binary = provider.find_binary()`. Bash wrapper (`exec bash` po exit) zostaje. Sudo askpass: tylko jeśli `provider.capabilities.supports_sudo` (Claude only).
- **Backward-compat:** `spawn_claude(config)` zachowane jako alias do `spawn_ai_cli(config | {"provider": "claude"})`.
- **Test:** `test_spawn_ai_cli.py::test_claude_argv_unchanged`, `test_copilot_argv_uses_provider_flags`, `test_unknown_provider_raises`.

### T2.2 — `ClaudeProvider.build_argv()` finalize (1 dzień)
- **Pliki:** `bterminal/providers/claude.py`
- **Zawartość:** pełna implementacja zgodna z obecnym `spawn_claude` (resume, skip_permissions, intro prompt jako positional arg, bash escaping).
- **Test:** golden output snapshot test — argv dla 4 typowych configów.

### T2.3 — `CopilotProvider.build_argv()` (2 dni)
- **Pliki:** `bterminal/providers/copilot.py`
- **Zawartość:**
  - Always: `--no-banner --no-mouse --plain-diff` (TUI-safe dla VTE).
  - Intro prompt: `-i "..."` (interactive z auto-promptem) lub `-p "..."` jeśli config ma `headless=true`.
  - `--resume <id>` jeśli `provider_options.resume=true` i sesja zna `last_session_id`.
  - `--yolo` jeśli `provider_options.skip_permissions=true`.
  - `--allow-tool` granular: jeśli `provider_options.allowed_tools` (advanced field, T4.3).
  - `--add-dir <project_dir>` jeśli ustawiony.
  - `--output-format json` jeśli `provider_options.json_output=true` (dla idle detection w T4.1).
- **Test:** `test_copilot_provider.py::test_argv_default`, `test_argv_with_resume`, `test_argv_with_yolo`, `test_argv_with_json_output`.

### T2.4 — Mock CLI scenario `copilot_basic.json` (1 dzień)
- **Pliki:** `tests/scenarios/copilot_basic.json`
- **Zawartość:** scenariusz emitujący Copilot-style output (banner mute, prompt prefix `>`). Uruchomi mock z argv jaki wygenerował `CopilotProvider.build_argv()`.
- **Test:** `test_mock_copilot_cli.py::test_mock_runs_with_copilot_argv` — fake binary wstawiony do PATH.

### T2.5 — `AISessionDialog` z dropdownem providera (3 dni)
- **Pliki:** `bterminal/ui/dialogs/ai_session.py` (nowy, rename z `claude_code.py`), `bterminal/ui/dialogs/claude_code.py` (shim).
- **Zawartość:**
  - GtkComboBox "AI Provider" na górze, populated z `registry.all()` filtered by `enabled=true`.
  - Pola dynamiczne: `name`, `project_dir`, `color`, `prompt`, `enabled_plugins` — provider-agnostic, zawsze widoczne.
  - Pola provider-specific: zostaną dodane w T2.6.
  - On `OK`: zapis z `provider=<selected>`, `provider_options={...}`.
- **Test:** `tests/test_ai_session_dialog.py::test_provider_dropdown_populated`, `test_default_provider_selected`, `test_provider_change_updates_fields`.
- **Smoke ręczny:** otwórz dialog, przełącz na Copilot, pola się zmieniają.

### T2.6 — Provider-specific dialog fields (2 dni)
- **Pliki:** `bterminal/ui/dialogs/ai_session.py`, `bterminal/providers/base.py` (`get_dialog_schema()` method).
- **Zawartość:**
  - `ClaudeProvider.get_dialog_schema()` → `[("resume", "checkbox", "Resume last session"), ("skip_permissions", "checkbox", "Skip permission prompts"), ("sudo", "checkbox", "Use sudo askpass")]`.
  - `CopilotProvider.get_dialog_schema()` → `[("skip_permissions", "checkbox", "Yolo mode (--yolo)"), ("model", "combo", ["auto", "claude-sonnet-4-5", "gpt-5", "opus-4-6"])]`.
  - Dialog renderuje pola z schema.
- **Test:** `test_ai_session_dialog.py::test_claude_fields_rendered`, `test_copilot_fields_rendered`.

### T2.7 — R7a: visual marker (emoji + color) (2 dni)
- **Pliki:** `bterminal/app.py:678-689` (`_TAB_EMOJIS` removal), `bterminal/ui/terminal_tab.py::update_tab_title`.
- **Zawartość:** tab label = `f"{provider.display.icon} {session_name}"`. Tab tooltip = `f"{provider.display.long_label}: {session_name}"`. Tab underline color = `provider.display.color` (jeśli session.color brak).
- **Test:** `test_tab_title.py::test_claude_emoji`, `test_copilot_emoji`, `test_local_emoji_fallback`.

### T2.8 — REST endpoint `POST /api/tabs/ai/{provider}` (1 dzień)
- **Pliki:** `bterminal/debug_rest.py:496-521`
- **Zawartość:** nowy endpoint przyjmujący `provider` w path. Body: session config. Backward-compat: `/api/tabs/claude` jako alias do `/api/tabs/ai/claude`.
- **Test:** `tests/test_rest_ai_tabs.py::test_open_claude_via_new_endpoint`, `test_open_copilot_via_new_endpoint`, `test_legacy_claude_endpoint_still_works`.

### T2.9 — `_init_ctx_in_project_dir` generuje `AGENTS.md` (1 dzień)
- **Pliki:** `bterminal/ctx/dialogs.py` (lub gdzie obecnie jest `_init_ctx_in_project_dir`)
- **Zawartość:** po wygenerowaniu `CLAUDE.md` — utwórz `AGENTS.md` jako symlink do `CLAUDE.md` (Linux: `os.symlink`). Jeśli symlink fails (np. cross-FS): fallback na hard copy.
- **Test:** `tests/test_ctx_init.py::test_creates_agents_md_symlink`, `test_agents_md_fallback_copy`.

### T2.10 — Mock-driven E2E test "open Copilot tab" (2 dni)
- **Pliki:** `tests/e2e/test_provider_switching.py`
- **Zawartość:** test scenariusza:
  1. Stub `copilot` binary w PATH (mock_ai_cli z scenariuszem copilot_basic).
  2. POST /api/tabs/ai/copilot z config.
  3. Assert: tab istnieje, tytuł zawiera 🤖, intro prompt został zapisany przez `record_feed`.
  4. Close tab, no crash.
- **Test:** `test_provider_switching.py::test_open_copilot_tab_via_rest`, `test_close_copilot_tab_no_crash`.

### T2.11 — Update launcher / install.sh (0.5 dnia)
- **Pliki:** `install.sh`, `bterminal-launcher`
- **Zawartość:** detekcja `copilot` binary w PATH przy install — jeśli brak, log "GitHub Copilot CLI not detected (optional, install: npm i -g @github/copilot)". Nie błąd.
- **Test:** ręczny smoke `./install.sh`.

### T2.12 — Tier 2 acceptance (1 dzień)
- **Smoke ręczny (z mockiem):**
  1. Stub `copilot` binary: `cp tools/mock_ai_cli ~/.local/bin/copilot`.
  2. Otwórz BTerminal, dodaj Copilot session, zaznacz w dialogu provider=Copilot.
  3. Otwórz tab — ikona 🤖, mock CLI odpowiada.
  4. Otwórz Claude session — wszystko działa identycznie jak przed Tier 1.
  5. Sprawdź `~/.config/bterminal/ai_sessions.json` — Claude i Copilot sesje koegzystują.

**Kryterium akceptacji Tier 2:**
- [ ] 219 + ~12 nowych testów = ~231 zielonych
- [ ] Mock Copilot tab otwiera się i ma poprawny tytuł
- [ ] Dialog konfiguracji session ma dropdown providera z 2 opcjami
- [ ] **L1 (live test):** odłożony — wymaga subskrypcji Copilot

---

## 5. Tier 3 — Stats + Auto-trigger + Rules (~20 dni)

**Cel:** stats bar pokazuje tokeny/cost dla obu providerów; rules injection działa w obu; auto-trigger zachowany dla Claude'a (Copilot skipped do T4.1).

### T3.1 — `SessionStatsBar` rozdzielony na strategy (3 dni)
- **Pliki:** `bterminal/ui/stats.py` → `bterminal/ui/stats/` (katalog), `__init__.py`, `base.py`, `claude.py`, `widget.py`.
- **Zawartość:**
  - `class AbstractStatsReader` — `read_session_tokens()`, `read_plan_usage()`, `read_session_cost()`.
  - `ClaudeStatsReader` — przeniesiona logika z obecnego `stats.py`.
  - `widget.SessionStatsBar` — używa `provider.create_stats_reader()` (factory z provider).
  - Capability `stats_bar=false` → bar nie tworzy się (obecnie i tak ukryty dla SSH/local).
- **Test:** istniejące testy stats muszą przejść; nowy `test_stats_strategy.py::test_claude_reader_used`.

### T3.2 — `CopilotStatsReader` (events.jsonl parser) (3 dni)
- **Pliki:** `bterminal/ui/stats/copilot.py`
- **Zawartość:**
  - `read_session_tokens()` — tail-f `~/.copilot/session-state/{uuid}/events.jsonl`, akumuluje tokeny z każdego `tool.execution_complete` event.
  - `read_session_cost()` — sumuje `session.shutdown.modelMetrics.*.requests.cost` (jeśli sesja zakończona) lub estymata na bazie `pricing` z config.
  - `read_plan_usage()` → zwraca None (capability `usage_api=false`).
- **Test:** `test_copilot_stats_reader.py` — parsuje fixture `tests/fixtures/copilot_events.jsonl` (~100 lines real-world-shaped sample). 5 testów: tokens, cost partial, cost final, missing file, malformed line graceful.

### T3.3 — Fixture `copilot_events.jsonl` (1 dzień)
- **Pliki:** `tests/fixtures/copilot_events.jsonl`, `tests/fixtures/copilot_events_partial.jsonl`
- **Zawartość:** ręcznie spreparowane próbki eventów na podstawie [oficjalnej dokumentacji](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/chronicle) i [blog jonmagic](https://jonmagic.com/posts/github-copilot-session-search-and-resume-cli/). 30+ events: session.start, multiple tool.execution_start/complete, session.shutdown z modelMetrics.
- **Komentarz w pliku:** `# TODO(L2): replace with real events.jsonl from live Copilot session when subscription available`.

### T3.4 — Mock CLI: emit `events.jsonl` directive (2 dni)
- **Pliki:** `tools/mock_ai_cli`
- **Zawartość:** dodaj scenario directive `emit_events_jsonl`: ścieżka + lista eventów do napisania. Mock pisze events do podanego pliku w trakcie pracy (z delays). Pozwala E2E testować live tail-f w Copilot scenariach.
- **Test:** `test_mock_emits_events_jsonl.py::test_writes_events_in_order`.

### T3.5 — Capability dispatch: `stats_bar` (1 dzień)
- **Pliki:** `bterminal/ui/terminal_tab.py` (gdzie tworzy się `_stats_bar`)
- **Zawartość:** `if provider.capabilities.stats_bar: self._stats_bar = SessionStatsBar(provider, self)`. Tooltip dla Copilota: "Plan usage not available for Copilot".
- **Test:** `test_stats_bar_dispatch.py::test_claude_shows_bar`, `test_copilot_shows_bar_no_plan_usage`, `test_local_no_bar`.

### T3.6 — Capability dispatch: `task_auto_trigger` (1 dzień)
- **Pliki:** `bterminal/ui/terminal_tab.py:587-650` (`_on_task_idle_timeout`)
- **Zawartość:** `if not provider.capabilities.task_auto_trigger: return False`. Dla Copilota w Tier 3: false → skip. W Tier 4: implementacja.
- **Test:** `test_auto_trigger_dispatch.py::test_claude_fires`, `test_copilot_skips_in_t3`.

### T3.7 — Capability dispatch: `rules_inject` (1 dzień)
- **Pliki:** `bterminal/ui/terminal_tab.py:365-413` (`_maybe_inject_rules`)
- **Zawartość:** `if not provider.capabilities.rules_inject: return False`. Dla Copilota: true (PTY feed_child działa identycznie). Faktyczne wywołanie `feed_child` provider-agnostic.
- **Test:** `test_rules_inject_dispatch.py::test_both_providers_inject`.

### T3.8 — `memory_wizard` dual-provider support (3 dni)
- **Pliki:** `tools/memory_wizard`
- **Zawartość:** dodaj flag `--provider {claude|copilot}`. Bez flagi: czytaj `~/.config/bterminal/ai_sessions.json`, znajdź sesję dla project, użyj jej providera. Parser per provider:
  - Claude: obecny JSONL parser.
  - Copilot: nowy parser czytający `events.jsonl` per session UUID.
- **Test:** `tests/test_memory_wizard_providers.py` — 3 testy: claude default, copilot z `--provider`, auto-detect.

### T3.9 — Token tracking dla Copilota w widget (1 dzień)
- **Pliki:** `bterminal/ui/stats/widget.py`
- **Zawartość:** widget renderuje `{tokens} | {cost}` dla Copilota (bez plan-usage gauge). UI hint kiedy stats_bar_no_plan_usage=true.
- **Test:** `test_stats_widget.py::test_copilot_renders_tokens_only`.

### T3.10 — Tier 3 acceptance (1 dzień)
- **Smoke ręczny (z mockiem):** otwórz Copilot tab z mock CLI emitującym events.jsonl, zweryfikuj że stats bar pokazuje tokeny rosnące w czasie. Otwórz Claude tab — bez regresji.
- **Update `docs/test-coverage-matrix.md`** — nowe wpisy R4a.2, R4a.3, R4a.4.

**Kryterium akceptacji Tier 3:**
- [ ] ~231 + ~12 nowych testów = ~243 zielonych
- [ ] `CopilotStatsReader` parsuje fixture poprawnie (cost sum > 0)
- [ ] Stats bar dla Claude działa bez zmian
- [ ] **L2/L3 (live tests):** odłożone — fixture-driven na razie

---

## 6. Tier 4 — Polish + Copilot auto-trigger (~15 dni)

**Cel:** feature parity Claude/Copilot na poziomie BTerminala. Mock + fixtures wystarczą do testów; live verification odłożona.

### T4.1 — Copilot idle detection przez tail-f `events.jsonl` (4 dni)
- **Pliki:** `bterminal/providers/copilot.py::detect_idle()`
- **Zawartość:** background thread tail-f `events.jsonl`. Idle = brak nowego `tool.execution_start` przez 10 s + ostatni event to `tool.execution_complete`. Sygnalizuje przez `GLib.idle_add` do main loop.
- **Test:** `tests/test_copilot_idle.py` — 4 testy z mock'iem fs:
  - `test_idle_after_complete_silence`: emit complete + sleep 11s → idle=true.
  - `test_not_idle_during_active_tool`: emit start bez complete → idle=false.
  - `test_recovers_from_truncated_jsonl`: malformed line → continue.
  - `test_session_shutdown_terminates`: shutdown event → idle=true permanently.
- **L2 follow-up:** komentarz `# TODO(L2): verify with live Copilot session that tool.execution_complete is reliably emitted`.

### T4.2 — Włącz `task_auto_trigger=true` dla Copilota + integration (1 dzień)
- **Pliki:** `bterminal/providers/defaults.json`, capability flag toggle.
- **Test:** `test_auto_trigger_dispatch.py::test_copilot_fires_after_idle` — używa T4.1 mock.

### T4.3 — Granular permissions UI dla Copilota (2 dni)
- **Pliki:** `bterminal/ui/dialogs/ai_session.py` (advanced field "Allowed tools" tylko dla Copilota).
- **Zawartość:** GtkTextView wieloliniowy, format: jedna reguła na linię (np. `shell(rm)`, `My-MCP-Server`). Walidacja: nieprawidłowe linie → czerwony underline.
- **Test:** `test_ai_session_dialog.py::test_copilot_advanced_allowed_tools`.

### T4.4 — Plan mode toggle UI dla Copilota (1 dzień)
- **Pliki:** `bterminal/ui/dialogs/ai_session.py` (checkbox "Start in plan mode" tylko dla Copilota).
- **Zawartość:** checkbox dodaje `--plan` do argv przy spawn.
- **Test:** `test_copilot_provider.py::test_argv_with_plan_mode`.

### T4.5 — Copilot SQLite session picker (BONUS — 3 dni, optional w MVP) (3 dni)
- **Pliki:** `bterminal/ui/sidebar.py` (jeśli rozszerzymy sidebar) lub nowy `bterminal/ui/panels/copilot_history.py`.
- **Zawartość:** czytaj `~/.copilot/session-store.db` (SQLite read-only), wystaw listę z FTS5 search. Klik "Resume in BTerminal" otwiera tab z `--resume <id>`.
- **Test:** `test_copilot_history_panel.py` — 3 testy z fixture SQLite db.
- **DECYZJA:** OPTIONAL — pomiń jeśli czas pęka.

### T4.6 — Cleanup backward-compat shimy (1 dzień)
- **Pliki:** `bterminal/models.py` (usuń `ClaudeSessionManager` alias), `bterminal/ui/terminal_tab.py` (usuń `spawn_claude` alias), `bterminal/debug_rest.py` (usuń `/api/tabs/claude` alias).
- **Pre-condition:** mija ≥1 release od Tier 1.
- **Test:** sprawdź że nie ma żadnego importu starych nazw w testach.

### T4.7 — Documentation + screenshots (1 dzień)
- **Pliki:** `README.md`, `docs/cli-provider-abstraction-analysis.md` (dopisek "Implemented").
- **Zawartość:** screenshot dialogu z dropdownem 2 providerów, screenshot dwóch tabów (Claude + Copilot) obok siebie z różnymi ikonami, opis jak skonfigurować Copilot.

### T4.8 — Tier 4 acceptance + final E2E (2 dni)
- **Smoke ręczny:** pełen workflow z mockami: dodaj sesję Claude, dodaj sesję Copilot, otwórz oba taby równocześnie, włącz auto-trigger w obu, dodaj zadanie w `tasks add bterminal "test"`, czekaj 11 s — oba taby powinny dostać `[AUTO-TRIGGER]`.
- **Test:** `tests/e2e/test_dual_provider_workflow.py::test_auto_trigger_fires_in_both_tabs`.

**Kryterium akceptacji Tier 4:**
- [ ] ~243 + ~10 nowych testów = ~253 zielonych
- [ ] Auto-trigger działa w mock-Copilot
- [ ] **L1, L2, L3 (live tests):** wciąż odłożone do momentu zakupu subskrypcji
- [ ] Dokumentacja zaktualizowana

---

## 7. Live verification follow-ups (po zakupie subskrypcji Copilot)

Po dostępie do żywego `copilot` CLI — uruchom 3 follow-up zadania:

### L1 — Smoke test żywego argv (0.5 dnia)
- Uruchom BTerminal, otwórz Copilot session bez mocka.
- Zweryfikuj że `copilot --no-banner --no-mouse --plain-diff -p "intro..."` faktycznie startuje i agent odpowiada.
- Jeśli krzyczy o brakujący flag — patch `CopilotProvider.build_argv()`.

### L2 — Verify events.jsonl format (1 dzień)
- Otwórz Copilot session, zrób 3-4 tool calls (file edit, shell, web).
- Skopiuj `~/.copilot/session-state/<uuid>/events.jsonl` do `tests/fixtures/copilot_events_real.jsonl`.
- Diff z fikcyjnym `copilot_events.jsonl` — patch parser jeśli pola się różnią.
- Update `tests/fixtures/copilot_events.jsonl` na podstawie reala.

### L3 — Verify cost reporting (0.5 dnia)
- Po zakończeniu sesji sprawdź `session.shutdown.modelMetrics.*.requests.cost`.
- Format: USD float czy PRU integer? Patch `CopilotStatsReader.read_session_cost()`.
- Test fixture update.

**Total live follow-up:** ~2 dni gdy subskrypcja będzie dostępna.

---

## 8. Mapa zależności (DAG)

```
T1.1 base ABC
  └─ T1.2 config loader
       └─ T1.3 ClaudeProvider
            └─ T1.4 CopilotProvider skeleton
                 └─ T1.5 ProviderRegistry
                      └─ T1.6 AISessionManager
                           └─ T1.7 Migration
                                └─ T1.8 Update call-sites
                                     └─ T1.9 intro prompt
                                          └─ T1.10 acceptance
                                               └─ T1.11 docs

T1.10 done → Tier 2 unblocked

T2.1 spawn refactor
  ├─ T2.2 Claude.build_argv (parallel)
  └─ T2.3 Copilot.build_argv
       └─ T2.4 mock copilot scenario
            └─ T2.10 E2E mock copilot tab

T2.5 dialog dropdown (parallel z T2.1)
  └─ T2.6 dialog fields
       └─ T2.12 acceptance

T2.7 visual marker (parallel)
T2.8 REST endpoint (parallel)
T2.9 AGENTS.md (parallel)
T2.11 install.sh (parallel)

T2.12 done → Tier 3 unblocked

T3.1 stats split
  └─ T3.2 CopilotStatsReader
       └─ T3.3 fixture jsonl
            └─ T3.5 stats_bar dispatch
                 └─ T3.9 widget tokens-only

T3.4 mock emit events_jsonl (parallel)
T3.6 auto-trigger dispatch (parallel)
T3.7 rules dispatch (parallel)
T3.8 memory_wizard providers (parallel)

T3.10 done → Tier 4 unblocked

T4.1 idle detection (krytyczne)
  └─ T4.2 enable auto-trigger Copilot
       └─ T4.8 dual-provider E2E

T4.3 granular permissions (parallel)
T4.4 plan mode (parallel)
T4.5 SQLite picker (optional, parallel)
T4.6 cleanup shimy (po release Tier 1)
T4.7 docs (parallel)
```

---

## 9. Ryzyka + mitigation (per Tier)

| Ryzyko | Tier | Mitigation |
|---|---|---|
| Migracja `claude_sessions.json` zniszczy dane | T1.7 | `.bak` backup + idempotency test + manual smoke przed merge |
| `spawn_claude` alias nie zachowuje 100% | T2.1 | snapshot test argv golden output |
| Mock CLI scenarios nie odzwierciedlają realu | T2.4, T3.3 | komentarze `TODO(L1/L2)` + follow-up po subskrypcji |
| `events.jsonl` parser missuje rzadkie eventy | T3.2 | graceful degradation — malformed line nie crashuje, log warning |
| Auto-trigger Copilot okazuje się niewykonalny przez tail-f | T4.1 | fallback: capability `task_auto_trigger=false` na zawsze, doc rationale |
| GTK threading bug w tail-f thread | T4.1 | `GLib.idle_add` dla każdego sygnału do main loop |
| Cleanup shimów psuje user-facing custom skrypty | T4.6 | release notes + 1-version warning period |

---

## 10. Estymacja final

| Tier | Zadania | Dni | Test count |
|---|---|---|---|
| Tier 1 | 11 | 13.5 | +12 |
| Tier 2 | 12 | 18.5 | +12 |
| Tier 3 | 10 | 17 | +12 |
| Tier 4 | 8 | 15 | +10 |
| Live (post-subscription) | 3 | 2 | +5 |
| **Total bez subskrypcji** | **41** | **64 dni** | **~46 nowych testów** |
| **Total z subskrypcją** | **44** | **66 dni** | **~51 nowych testów** |

**Buffer:** zarezerwuj +20 % na bugfixy / GTK quirks / nieoczekiwane = ~80 dni razem.

---

## 11. Tasks tracking

Wszystkie zadania (T1.1 – T4.8 + L1 – L3) są dodane do `tasks` CLI dla projektu `bterminal`. Przykład dodania:

```bash
tasks add bterminal "T1.1 Provider base ABC + dataclasses (1d)"
tasks add bterminal "T1.2 Provider config loader + defaults.json (1d)"
# ... etc
```

Zadania mają **pełne powiązanie** z tym dokumentem — odwołuj się do sekcji 3-7 podczas pracy nad konkretnym zadaniem. Po wykonaniu zadania:

```bash
tasks done bterminal <task_id>
```

---

*Koniec planu. Gotowe do implementacji w trybie task-driven (auto-trigger może uruchamiać kolejne T#).*
