# BTerminal — Test Coverage Matrix

**Źródło:** REQUIREMENTS.md (66 wymagań top-level, ~200+ sub-criteria)
**Test suite:** 32 plików `tests/test_*.py` + `tests/action_graph/` + `tests/e2e/`
**Data:** 2026-05-06 (po Tier 1 — provider abstraction)
**Cel:** mapa `R<N>` → `test_name(s)` z identyfikacją luk pokrycia.

**Konwencja:**
- ✅ **Covered** — bezpośredni test asercjuje wymaganie
- ⚠️ **Partial** — niektóre aspekty pokryte, inne nie
- ❌ **Missing** — brak testu pokrywającego
- 🟦 **N/A** — wymaganie negatywne / czysto-doc / nie wymaga testu
- 🔴 **Not implemented** — wymaganie w doc'u, ale kod nie napisany

---

## Statystyki ogólne

| Status | Liczba R<N> | % |
|--------|-------------|---|
| ✅ Covered | 17 | 26% |
| ⚠️ Partial | 14 | 21% |
| ❌ Missing | 26 | 39% |
| 🟦 N/A | 2 | 3% |
| 🔴 Not implemented | 7 | 11% |
| **TOTAL** | **66** | 100% |

**Wniosek po Tier 1 (2026-05-06):** ~47% pokrycia (covered + partial,
+9pp vs 2026-05-04). Tier 1 dostarczył pełną warstwę provider
abstraction (R4 / R4a / R4b) z 70+ unit testami + E2E acceptance,
plus 1:1 port stats reader / OAuth usage / argv builder do
ClaudeProvider (R8.1, R8.2, R32-R34).

Pozostałe duże luki: UI flows (panele, dialogi, theme), end-to-end
task auto-trigger (R21), intro prompt structure (R11), CLI tools
(ctx/tasks/consult/memory_wizard/claude_log). Provider abstraction
foundation jest gotowa — Tier 2 (T2.x) doda spawn dispatch i dialog
z dropdownem providera; Tier 3 (T3.x) podzieli stats bar na strategie
i doda Copilot events.jsonl parser.

---

## §1. Application lifecycle

| R | Status | Test | Gap |
|---|--------|------|-----|
| R1.1 strict argparse | ❌ | — | test_entry_point ma `--help`/`--debug-rest`, brak rejection unknown flag |
| R1.2 drop env var | ❌ | — | test nie weryfikuje że BTERMINAL_DEBUG_REST jest IGNOROWANY |
| R1.3 PATH prefix | ❌ | — | brak testu spawn'u subprocess z czystym PATH |
| R1.4 NON_UNIQUE | ❌ | — | brak testu uruchamiania 2 instancji równolegle |
| R1.5 window <3s | ⚠️ | conftest fixture (HEALTH_TIMEOUT_SEC=10s implicit) | brak explicite asercji czasu boot |
| R1.6 update check 3s | ❌ | — | brak testu trigger 3s po starcie |
| R1.f1 missing options.json | ⚠️ | test_config::test_options_default_when_missing (unit-level) | brak BTerminal-startup test |
| R1.f2 corrupt options self-heal | 🔴❌ | — | NIE ZAIMPLEMENTOWANE + brak testu |
| R1.f3 no DISPLAY | 🟦 | — | testy używają xvfb, headless OOS |
| R2.1 sidecar termination | ✅ | test_sidecar_lifecycle::test_stop | atexit hook nie testowany |
| R2.2 REST shutdown | ✅ | test_quit | — |
| R2.3 no zombie processes | ⚠️ | test_quit (implicit) | brak explicit ps zombie check |
| R2.4 _collect_claude_log | ❌ | — | brak testu czy log jest collected na close |

---

## §2. Session management

| R | Status | Test | Gap |
|---|--------|------|-----|
| R3.1 atomic save | ⚠️ | test_models::test_jlm_add_persists | brak fault injection (crash mid-write) |
| R3.2 host validation | ✅ | test_models::test_session_manager_requires_host | — |
| R3.3 fields preserved | ⚠️ | test_models (add roundtrip) | brak coverage dla `key_path`, `port`, `folder`, `macros` |
| R3.4 UUID id | ✅ | test_models::test_jlm_add_persists_and_assigns_id | — |
| R3.5 sidebar reorder | ❌ | — | UI drag&drop nie testowany |
| R3.f1 no host | ✅ | test_models | — |
| R3.f2 corrupt JSON | ✅ | test_models::test_jlm_corrupt_json_falls_back_to_empty | — |
| R3a.1-6 in-memory passwords | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
| R4.1-3 ai_sessions.json | ✅ | test_ai_session_manager (12), test_migration_ai_sessions (11), e2e/test_tier1_acceptance (4) | — (Tier 1) |
| R4a provider capabilities | ✅ | test_providers_base (13), test_providers_config_loader (13), test_provider_registry (17), test_claude_provider (24), test_copilot_provider (13) | — (Tier 1) |
| R4b migration | ✅ | test_migration_ai_sessions (11), e2e/test_tier1_acceptance (4) — idempotent + .bak backup + provider_options wrap | — (Tier 1) |
| R5.1-4 macros | ❌ | — | brak żadnych testów macro |
| R6.1-3 folder organization | ❌ | — | brak testów |

---

## §3. Terminal tab lifecycle

| R | Status | Test | Gap |
|---|--------|------|-----|
| R7.1 widget added | ✅ | test_tabs::test_open_local_tab_increments_count | — |
| R7.2 VTE inside | ⚠️ | implicit | brak palette/font asercji |
| R7.3 tab title format | ❌ | — | brak asercji decorated name |
| R7.4 close button | ❌ | — | UI element nie weryfikowany |
| R7.5 reorderable | ❌ | — | brak testu drag tab |
| R7.6 stats bar on Claude | ⚠️ | test_intro_prompt (indirect) | brak explicit stats bar visibility check |
| R7a visual provider marker | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
| R8.1 binary path search | ✅ | test_claude_provider::test_find_binary_returns_first_match / skips_nonexecutable / glob_resolves | — (Tier 1, ClaudeProvider.find_binary) |
| R8.2 argv composition | ✅ | test_claude_provider::test_build_argv_minimal / with_resume / with_skip_permissions / full_session_with_intro / reads_provider_options / returns_empty_when_binary_missing | — (Tier 1, ClaudeProvider.build_argv) |
| R8.3 intro feed | ⚠️ | test_intro_prompt (content), brak feed mechanism | feed_child capture wymaga vte_capture (Etap B) |
| R8.4 exec bash fallback | ❌ | — | wymagałoby mock claude exit |
| R8.5 log dir creation | ❌ | — | brak asercji `<project>/claude_log/` mkdir |
| R8.f1 no claude binary | ❌ | — | brak fault test |
| R8.f2 missing project_dir → dialog | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
| R8a AI CLI errors → terminal | ❌ | — | brak testu stderr forward |
| R9.1 SIGTERM child | ⚠️ | test_tabs (close), brak ps assertion | brak weryfikacji process killed |
| R9.2 _collect_claude_log on close | ❌ | — | brak testu |
| R9.3 sidecar release | ✅ | test_per_tab_plugins::test_sidecar_refcount | — |
| R9.4 widget destroy | ⚠️ | implicit | — |
| R9.5 page removed | ✅ | test_tabs | — |
| R9.f1 409 on autorun | ⚠️ | conftest używa ?force=true | brak explicit 409 test |
| R9.f2 GUI confirm dialog | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |

---

## §4-5. AI CLI integration + Intro prompt

| R | Status | Test | Gap |
|---|--------|------|-----|
| R10 provider abstraction | ✅ | test_providers_* (5 plików, 80 testów) — ABC + capabilities + config loader + registry + Claude/Copilot providers | — (Tier 1 foundation; spawn dispatch in T2, stats split in T3) |
| R10a provider-aware intro header | ✅ | test_intro_prompt_per_provider (8 testów) — long_label substitution + registry lookup + fallback | — (Tier 1, T1.9) |
| R11.1 markdown sections | ⚠️ | test_intro_prompt::test_intro_includes_only_tab_enabled_sidecars (sidecar section only) | brak full structure assertion |
| R11.2 section order | ❌ | — | brak ordering test (header → rules → ctx → ...) |
| R11.3 separator `\n\n` | ❌ | — | brak |
| R11.4 enabled_plugins default | ✅ | test_intro_prompt (test obu cases: None i explicit) | — |
| R12.1 _fetch_ctx_output | ❌ | — | brak testu subprocess call |
| R12.2 first section | ❌ | — | brak ordering test |
| R12.3 ctx CLI absent fallback | ❌ | — | brak fault test |
| R13 tools help block | ❌ | — | brak testu treści tools help |

---

## §6. Rules injection

| R | Status | Test | Gap |
|---|--------|------|-----|
| R14 initial inject | ⚠️ | test_intro_prompt (rules part of intro) | brak explicit rules section assertion |
| R15.1-5 periodic re-injection | ❌ | — | **brak end-to-end test count==inject_every → idle → injection** (krytyczna luka) |

---

## §7. Ctx subsystem

| R | Status | Test | Gap |
|---|--------|------|-----|
| R16 project resolution | ✅ | test_ctx_helpers (5 testów: smart_name, sessions match, parent walk, empty, registered) | — |
| R17.1-3 ctx wizard | ❌ | — | UI flow nie testowany |
| R18.1-4 ctx CRUD | ❌ | — | UI panel actions nie testowane |
| R19.1-3 export/import | ❌ | — | **round-trip integrity test obowiązkowy** (per Q19.1, MISSING) |

---

## §8. Tasks subsystem

| R | Status | Test | Gap |
|---|--------|------|-----|
| R20.1-3 task schema | ❌ | — | brak unit testów schemy DB |
| R21.1 autorun=1 written | ❌ | — | brak |
| R21.2-7 auto-trigger lifecycle | ❌ | — | **NAJBARDZIEJ KRYTYCZNA LUKA** — brak end-to-end test pętli auto-trigger |
| R21.f1-3 failure modes | ❌ | — | brak |
| R22.1-4 task editing | ❌ | — | UI nie testowany |

---

## §9. Memory + log retention

| R | Status | Test | Gap |
|---|--------|------|-----|
| R23.1-3 per-project rules | ❌ | — | brak testów memory panel |
| R23a log retention 20 | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
| R24 memory wizard | ❌ | — | CLI tool nie testowany |

---

## §10-11. Plugins + Sidecars

| R | Status | Test | Gap |
|---|--------|------|-----|
| R25.1-4 plugin loader | ⚠️ | test_per_tab_plugins (indirectly), conftest setup creates fake plugin | brak explicit unit test loadera |
| R25.f1-2 fail handling | ❌ | — | brak fault injection |
| R26.1-4 plugin ABC contract | ✅ | test_plugin_contracts (3 testy: ABC methods, defaults, subclass override) | — |
| R27.1-3 hot toggle | ✅ | test_hot_toggle::test_plugin_hot_disable_removes_from_sidebar | — |
| R27a globalny disable + active tabs | 🔴❌ | — | NIE ZAIMPLEMENTOWANE (decyzja, nie code) |
| R28.1-3 sidecar manifest | ✅ | test_plugin_contracts (3 manifest testy), test_manifests, test_sidecar_discovery (3) | — |
| R28a task_bound flag | 🔴❌ | — | NIE ZAIMPLEMENTOWANE (rename auto_start) |
| R29.1-5 sidecar lifecycle | ✅ | test_sidecar_lifecycle (3), test_plugin_contracts (4 runner testy + health) | — |
| R30.1-4 per-tab refcount | ✅ | test_per_tab_plugins::test_sidecar_refcount, test_per_tab_enabled_plugins_isolation | — |
| R30a missing plugin → dialog | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |

---

## §12. Stats bar [Claude-only → provider-aware after T3]

| R | Status | Test | Gap |
|---|--------|------|-----|
| R31.1-3 stats display | ❌ | — | UI panel nie testowany (widget render — T3.9 / T3.10) |
| R32.1-4 session log reader | ✅ | test_claude_provider::test_parse_session_stats_accumulates_tokens / skips_malformed / missing_file_returns_zero | — (Tier 1, ClaudeProvider.parse_session_stats) |
| R33.1-3 cost calculation | ✅ | test_claude_provider::test_parse_session_stats_accumulates_tokens (cost asercja) / unknown_model_uses_default_pricing | — (Tier 1, ClaudeProvider.calculate_cost) |
| R34.1-5 plan usage API | ✅ | test_claude_provider::test_fetch_plan_usage_returns_none_when_creds_missing / capability_off / token_expired | — (Tier 1, ClaudeProvider.fetch_plan_usage) — happy-path API call wymaga mock'a serwera (post-Tier 1) |

---

## §13-16. UI panels (Skills/Files/Git/Consult)

| R | Status | Test | Gap |
|---|--------|------|-----|
| R35 skills discovery | ❌ | — | brak |
| R36 files browser | ❌ | — | brak |
| R37 diff with commit | ❌ | — | brak |
| R38 git panel | ❌ | — | brak (live FileMonitor nie testowany) |
| R39 consult panel | ❌ | — | brak |

**Wszystkie panele** wymagają vte_capture / mock provider / screenshot
diff infra (Etap B). Currently żadnych nie testujemy GUI-side.

---

## §17. Theme system

| R | Status | Test | Gap |
|---|--------|------|-----|
| R40.1-3 Catppuccin palettes | ✅ | test_config (key consistency, palette length 16) | — |
| R41.1-4 theme toggle | ❌ | — | brak testu toggle + persist + re-color |

---

## §18. Auto-update + errata

| R | Status | Test | Gap |
|---|--------|------|-----|
| R42.1-7 update check | ⚠️ | test_updater (_load_local_errata, _check_for_updates safe-no-repo) | brak end-to-end update flow |
| R42a pre-update consent | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
| R43.1-2 errata viewer | ⚠️ | test_updater::test_load_local_errata_chronological | brak GUI dialog test |

---

## §19. Debug REST API

| R | Status | Test | Gap |
|---|--------|------|-----|
| R44.1-6 REST bootstrap | ✅ | test_health, test_auth (3 testy), test_audit_log, test_idle_timeout | — |
| R45.1 GET routes (10) | ✅ | test_health, test_screenshot_endpoint, test_intro_prompt | brak coverage `/state`, `/debug/log`, sidecars/{n}/health |
| R45.2 PUT route | ✅ | test_per_tab_plugins (PUT plugins) | — |
| R45.3 POST routes (15) | ✅ | test_quit, test_tabs (4), test_per_tab_plugins (refcount), test_sidecar_lifecycle (start/stop) | brak coverage `simulate_prompt`, `force_idle`, `tabs/claude` |
| R45.4 Bearer auth | ✅ | test_auth (3 testy: no token, wrong token, correct token) | — |

---

## §20. Configuration persistence

| R | Status | Test | Gap |
|---|--------|------|-----|
| R46.1-3 options | ✅ | test_config (4 testy: defaults, roundtrip, corrupt, partial merge) | — |
| R47.1-3 repo path | ❌ | — | brak testu _repo_path_file |

---

## §21. Installer + keyboard shortcuts

| R | Status | Test | Gap |
|---|--------|------|-----|
| R48.1-6 install.sh | ❌ | — | **brak Docker test fresh install** (Q48.1 obowiązkowy) |
| R49 errata feed | ❌ | — | brak |
| R49a keyboard shortcuts dialog | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
| R49b window state not persisted | 🟦 | — | negative requirement, nie wymaga testu |

---

## §22. CLI tools

| R | Status | Test | Gap |
|---|--------|------|-----|
| R50 ctx CLI | ❌ | — | brak testów tools/ctx |
| R51 tasks CLI | ❌ | — | brak testów tools/tasks |
| R52 consult CLI | ❌ | — | brak testów tools/consult |
| R53 memory_wizard CLI | ❌ | — | brak testów tools/memory_wizard |
| R54 claude_log CLI | ❌ | — | brak testów tools/claude_log |

---

# Top priorities — krytyczne luki coverage

## 🔴 P0 (blokery jakości)

1. **R21 task auto-trigger end-to-end** — pętla autorun/claim/idle/inject
   to KLUCZOWY mechanizm. Brak testu = każdy refactor może to subtelnie
   zepsuć (mieliśmy już regresję!).

2. **R15 rules periodic re-injection** — `count == inject_every`,
   `_inject_pending`, idle timeout, dwustopniowa wiadomość. Brak
   end-to-end testu.

3. **R48 install.sh fresh install** — Docker test obowiązkowy per
   Q48.1, zero coverage obecnie.

## 🟡 P1 (high-value)

4. **R11 intro prompt structure** — order sekcji, separatory, full
   content. Krytyczne dla provider abstraction.

5. **R8 Claude tab spawn** — argv composition, intro feed mechanism,
   binary path search candidates.

6. **R19 ctx export/import round-trip** — Q19.1 explicite obowiązkowy.

7. **R32-34 stats bar reader + cost + usage API** — wszystkie Claude-specific
   logic bez testów.

## 🟢 P2 (nice-to-have)

8. **R5 macros** — UI flow, niski risk regresji
9. **R36-39 UI panels** (Files/Git/Consult/Skills) — wymagają visual
   regression infra
10. **R17 ctx wizard** — UI flow

## 🔧 Wymaga implementacji (R<N> NIE jest jeszcze w kodzie)

- R3a passwords in-memory
- R4/R4a/R4b provider abstraction + migration
- R7a visual provider marker
- R8.f2 missing project_dir dialog
- R9.f2 GUI close confirm
- R23a log retention
- R27a globalny disable behavior
- R28a `task_bound` rename
- R30a missing plugin per-tab dialog
- R42a pre-update consent
- R49a keyboard shortcuts dialog

---

# Foundation needed for missing tests

Większość P0/P1 luk wymaga **vte_capture fixture + mock_ai_cli** (Etap B):
- R8.3 intro feed mechanism — nie da się bez vte_capture
- R11 intro structure — wymaga capture intro prompt'u feedowanego do VTE
- R15 periodic injection — wymaga mock claude który "myśli" + idle timer
- R21 auto-trigger loop — wymaga mock claude reagujący na [AUTO-TRIGGER]
- R32 stats reader — wymaga mock claude generujący JSONL session log

**Wniosek:** Etap B (testing infrastructure) odblokuje **15+ testów P0/P1**.
Bez niego dalsza praca testowa jest mocno ograniczona.

---

# Plan akcji — kolejność (rekomendowana)

1. **Etap A (R1 quick wins)** — odpalić, regresja istniejąca trzyma
2. **Etap B (vte_capture + mock_ai_cli)** — fundament dla P0/P1
3. **Wykorzystać Etap B do napisać P0 testów (R21, R15, R11)**
4. **R48 Docker installer** — orto orogonalny, można w międzyczasie
5. **Etap C (R3a passwords)** — refactor z safety net
6. **Provider abstraction (R4) + dopisać testy** — biggest payoff

**Spodziewane coverage po P0+P1:** ~60% (z obecnych 38%).
**Pełne pokrycie wymaga visual regression infra** (Etap A panelu z plan'u).

---

# Tier 1 milestone — provider abstraction (2026-05-06)

**Status:** **ZAKOŃCZONY**, 11 zadań (T1.1 → T1.11), ~80 nowych testów, +9pp pokrycia (38% → 47%).

**Co dostarczone:**

| Zadanie | Pliki | Liczba testów |
|---------|-------|---------------|
| T1.1 Provider base ABC + dataclasses | bterminal/providers/base.py, tests/test_providers_base.py | 13 |
| T1.2 Provider config loader + defaults.json | bterminal/providers/__init__.py, bterminal/providers/defaults.json, tests/test_providers_config_loader.py | 13 |
| T1.3 ClaudeProvider 1:1 z legacy | bterminal/providers/claude.py, tests/test_claude_provider.py | 24 |
| T1.4 CopilotProvider skeleton | bterminal/providers/copilot.py, tests/test_copilot_provider.py | 13 |
| T1.5 ProviderRegistry singleton | bterminal/providers/__init__.py, tests/test_provider_registry.py | 17 |
| T1.6 AISessionManager rename + provider field | bterminal/models.py (rename), tests/test_ai_session_manager.py | 12 |
| T1.7 Migracja claude_sessions.json → ai_sessions.json | bterminal/models.py, bterminal/config.py, tests/test_migration_ai_sessions.py | 11 |
| T1.8 ai_config / claude_config alias property | bterminal/ui/terminal_tab.py + 5 plików, tests/test_terminal_tab_config_alias.py | 6 |
| T1.9 Provider-aware intro prompt header | bterminal/helpers.py, bterminal/ui/dialogs/claude_code.py, tests/test_intro_prompt_per_provider.py | 8 |
| T1.10 Tier1 acceptance E2E + license helper | tests/_subprocess_helpers.py, tests/e2e/test_tier1_acceptance.py | 4 |
| T1.11 (this doc) | docs/test-coverage-matrix.md | — |
| **Razem** | **~30 plików zmienionych/nowych** | **+121 nowych testów** |

**Nowe wymagania pokryte:** R4 / R4a / R4b (provider abstraction core) — wszystkie ✅.
**Re-pokryte wymagania (wcześniej ❌):** R8.1 (find_binary), R8.2 (build_argv), R32 (stats reader), R33 (cost calc), R34 (plan usage API).
**Nowe wymaganie:** R10a (provider-aware intro header) ✅ z T1.9.

**Suite stats po Tier 1:**
- Fast suite: **505 passed** (było 354 przed T1)
- Slow suite: **507 passed** + 1 pre-existing flaky niezwiązany z T1
- Run time fast: ~12s (z ~10s pre-T1 → +2s na 121 nowych testów)

**Bonus efekty Tier 1:**
- Centralized license pre-seed helper (`tests/_subprocess_helpers.py::seed_license`) — 3 fixture'y subprocess'ów BTerminal teraz używają jednego helpera, zamiast inline duplikat (response na user feedback "Cały czas wyskakują mi okna z umową licencyjną").
- Self-healed pre-existing failure: `test_idle_timeout` był zfailowany na master (license dialog) — teraz zielony.

**Następny etap (Tier 2):** spawn dispatch, AISessionDialog z dropdownem providera, R7a visual marker, REST `/api/tabs/ai/{provider}`. Zadania T2.1 → T2.12.

---

# Tier 2 milestone — spawn + dialog + REST + visual marker (2026-05-06)

**Status:** **ZAKOŃCZONY**, 12 zadań (T2.1 → T2.12), ~75 nowych testów, +6pp pokrycia (47% → ~53%).

**Co dostarczone:**

| Zadanie | Pliki | Liczba testów |
|---------|-------|---------------|
| T2.1 spawn_ai_cli refactor + provider dispatch | bterminal/ui/terminal_tab.py, tests/test_spawn_ai_cli.py | 19 |
| T2.2 ClaudeProvider.build_argv finalize z snapshot | tests/test_claude_provider.py (T2.2 section) | 7 |
| T2.3 CopilotProvider.build_argv z TUI-safe flags | bterminal/providers/copilot.py, tests/test_copilot_provider.py | 12 |
| T2.4 Mock CLI scenario copilot_basic.json | tests/scenarios/copilot_basic.json, tests/test_mock_copilot_cli.py | 9 |
| T2.5 AISessionDialog z dropdown providera | bterminal/ui/dialogs/ai_session.py (new), tests/test_ai_session_dialog.py | 17 |
| T2.6 Provider-specific dialog fields (get_dialog_schema) | bterminal/providers/{claude,copilot}.py, tests/test_ai_session_dialog.py (T2.6 section) | 7 |
| T2.7 R7a visual marker (compute_tab_label) | bterminal/ui/terminal_tab.py, bterminal/app.py, tests/test_tab_label.py | 13 |
| T2.8 REST /api/tabs/ai/{provider} + alias /api/tabs/claude | bterminal/debug_rest.py, tests/test_rest_ai_tabs.py | 10 |
| T2.9 AGENTS.md symlink to CLAUDE.md | bterminal/ctx/{helpers,dialogs}.py, tests/test_ctx_init.py | 10 |
| T2.10 E2E: open Copilot tab via REST z mock CLI | tests/e2e/test_provider_switching.py | 3 |
| T2.11 install.sh detekcja copilot binary | install.sh, tests/test_install_copilot_detection.py | 7 |
| T2.12 Tier2 acceptance E2E (Claude+Copilot coexist) | tests/e2e/test_tier2_acceptance.py | 5 |
| **Razem** | **~12 plików zmienionych/nowych** | **+119 nowych testów** |

**Nowe wymagania pokryte:**
- R7a (visual provider marker) — `compute_tab_label()` + emoji + tooltip + Pango color ✅
- R8.f1 (no claude binary) — `_build_binary_not_found_script` generalized for any provider ✅
- R10.f (provider abstraction full path) — REST → registry → spawn dispatch ✅

**Suite stats po Tier 2:**
- Fast suite: **619 passed** (było 505 po Tier 1 → +114)
- Run time fast: ~13s (było ~12s — +1s na 119 nowych testów)
- Wszystkie scenariusze E2E: 12 dedykowanych testów subprocess'owych (T2.4, T2.10, T2.12 + tier1_acceptance + per_tab_plugin_gating + REST ai_tabs)

**Bonus efekty Tier 2:**
- **Mock CLI infrastructure** rozszerzona: `tests/scenarios/copilot_basic.json` + reuse `mock_ai_cli` z PATH stub'em (T2.4, T2.10).
- **Pure-helper pattern** w 4 modułach (test bez GTK): `compute_tab_label`, `_build_provider_combo_items`, `_split_provider_options_from_data`, `_flatten_session_for_legacy_dialog`.
- **Backward-compat** w 4 punktach: `claude_config` property → `ai_config`, `spawn_claude` → `spawn_ai_cli`, `/api/tabs/claude` → `/api/tabs/ai/claude`, `ClaudeCodeDialog` ← `AISessionDialog` jako parent. Cleanup zaplanowany w T4.6.
- **`AGENTS.md` symlink** (T2.9) — Copilot CLI czyta ten sam context co Claude bez user maintenance.

**Następny etap (Tier 3):** stats split na strategy, CopilotStatsReader (events.jsonl), capability dispatch dla stats_bar/auto-trigger/rules_inject, memory_wizard dual-provider. Zadania T3.1 → T3.10.

---

# Tier 3 milestone — stats split + capability dispatch + memory_wizard (2026-05-06)

**Status:** **ZAKOŃCZONY**, 10 zadań (T3.1 → T3.10), ~140 nowych testów, +5pp pokrycia (~53% → ~58%).

**Co dostarczone:**

| Zadanie | Pliki | Liczba testów |
|---------|-------|---------------|
| T3.1 SessionStatsBar split na strategy | bterminal/ui/stats/{base,claude,widget,__init__}.py, tests/test_stats_strategy.py | 16 |
| T3.2 CopilotStatsReader (events.jsonl parser) | bterminal/ui/stats/copilot.py, tests/test_copilot_stats_reader.py | 15 |
| T3.3 Fixture copilot_events.jsonl + partial | tests/fixtures/{copilot_events,copilot_events_partial,README.md} | 2 |
| T3.4 Mock CLI emit_events_jsonl directive | tools/mock_ai_cli, tests/scenarios/copilot_with_events.json, tests/test_mock_emits_events_jsonl.py | 10 |
| T3.5 Capability dispatch stats_bar | bterminal/ui/stats/__init__.py (factory), tests/test_stats_bar_dispatch.py | 12 |
| T3.6 Capability dispatch task_auto_trigger | bterminal/ui/terminal_tab.py (helper + check), tests/test_auto_trigger_dispatch.py | 10 |
| T3.7 Capability dispatch rules_inject | bterminal/ui/terminal_tab.py (helper + check), tests/test_rules_inject_dispatch.py | 12 |
| T3.8 memory_wizard --provider flag | tools/memory_wizard, tests/test_memory_wizard_providers.py | 23 |
| T3.9 SessionStatsBar tokens-only mode | bterminal/ui/stats/widget.py, tests/test_stats_widget_options.py | 15 |
| T3.10 Tier3 acceptance E2E | tests/e2e/test_tier3_acceptance.py | 4 |
| **Razem** | **~10 plików zmienionych/nowych** | **+119 nowych testów** |

**Nowe wymagania pokryte:**
- R4a.2 (`stats_bar` capability) — `create_stats_reader_for_ai_config` + factory dispatch ✅
- R4a.3 (`task_auto_trigger` capability) — `should_run_auto_trigger` helper ✅
- R4a.4 (`intro_prompt` / generic capability gating) — pure-helper pattern w 3 dispatch funkcjach ✅

**Suite stats po Tier 3:**
- Fast suite: **739 passed** (było 624 po Tier 2 → +115)
- Run time fast: ~13s (bez wzrostu)
- E2E suite: 6 dedykowanych acceptance tests przez 3 Tiery (tier1_acceptance + tier2_acceptance + provider_switching + per_tab_plugin_gating + tier3_acceptance + REST ai_tabs)

**Nowe pure helpers (testowalne bez GTK):**
- `bterminal/ui/stats/__init__.py::create_stats_reader_for_ai_config(ai_config, registry)` — reader factory.
- `bterminal/ui/stats/__init__.py::stats_widget_options_for_ai_config(ai_config, registry)` — widget kwargs.
- `bterminal/ui/stats/widget.py::_hidden_label_keys_for_options(hide_plan_usage)` — UI label gating.
- `bterminal/ui/terminal_tab.py::should_run_auto_trigger(ai_config, registry)` — auto-trigger gate.
- `bterminal/ui/terminal_tab.py::should_inject_rules(ai_config, registry)` — rules injection gate.
- `tools/memory_wizard::_detect_provider_from_sessions / _resolve_provider / _build_ai_ask_argv` — provider dispatch w CLI tool.

**Capability flips dla Copilot w Tier 3:**
- T3.5: `session_log` + `cost_in_log` + `stats_bar` → True
- T3.7: `rules_inject` → True
- Nadal False: `task_auto_trigger` (T4.1), `granular_permissions` (T4.3), `plan_mode`/`autopilot` (T4.4), `mcp_support` (T5+).

**Następny etap (Tier 4):** auto-trigger dla Copilota (events.jsonl tail-f thread), granular permissions UI, plan mode UI, dual-provider workflow E2E. Zadania T4.1 → T4.8.

---

# GUI Control-Flow Graph + E2E Test Plan (#155, 2026-05-08)

**Cel:** kompletna mapa wszystkich GUI flows BT do automatycznych testów E2E.
Zadania #156 (live monitor framework) i #157-#162 (per-menu E2E) implementują
testy w/g tego grafu. Każdy node = punkt entry; każda edge = user action; każdy
leaf = post-state weryfikowany screenshotem + REST asercjami + log markerami.

## Notacja
- `■` — entry point (menu / button / shortcut)
- `→` — user action (click / key)
- `⊡` — emergent dialog / window
- `✓` — covered by automated E2E test (post-#161 finished)
- `~` — partial (manual smoke present, automated test missing)
- `✗` — uncovered (target task indicated)

## 1. Top-level menubar

```
■ File menu (Alt+F)                            target task
├─ → New local tab          → opens VTE shell tab        ✗ #157
├─ → New SSH session        → SSHSessionDialog           ✗ #157
├─ → New Claude Code        → AISessionDialog            ✗ #157
├─ → Options                → OptionsDialog              ✓ #154
└─ → Quit (Ctrl+Q)          → app exit                   ✗ #157

■ View menu (Alt+V)
├─ → Toggle sidebar (Ctrl+B)    → sidebar.set_visible    ✗ #158
├─ → Toggle Git panel (Ctrl+G)  → git panel slide        ✗ #158
├─ → Toggle theme               → CSS swap dark↔light    ✗ #158
├─ → Sessions panel             → stack child=sessions   ✗ #158
├─ → Ctx panel                  → stack child=ctx        ✗ #158
├─ → Consult panel              → stack child=consult    ✗ #158
├─ → Tasks panel                → stack child=tasks      ✗ #158
└─ → Plugins panel              → stack child=plugins    ✗ #158

■ Tools menu (Alt+T) — F10 fallback (Alt+T mnemonic broken, see #150)
├─ → Check for updates      → updater.check_modal        ~ #159
├─ → Errata                 → ErrataDialog               ~ #159
├─ → Diagnostics            → DiagnosticsDialog          ~ #159
└─ → Install dependencies   → InstallerWizard            ~ #159
```

## 2. Sidebar — primary CRUD surface

```
■ Sidebar (panel: Sessions)
├─ ⊡ Add ▼ button (split-button)
│   ├─ → SSH session         → SSHSessionDialog          ✗ #160
│   ├─ → Claude Code         → AISessionDialog           ✗ #160
│   └─ → Folder              → FolderDialog              ✗ #160
├─ ⊡ Edit button (active row)
│   └─ → opens dialog (SSH/AI/Folder per row type)       ✗ #160
├─ ⊡ Delete button → ConfirmDialog → row removed         ✗ #160
└─ ⊡ Right-click context menu
    ├─ → Connect              → spawn tab                ✗ #161 (per-provider)
    ├─ → Edit                 → see Edit above           ✗ #160
    ├─ → Delete               → see Delete above         ✗ #160
    ├─ → Run as ▸ (submenu)  → spawn tab w/ provider override
    │   ├─ Claude                                         ✗ #161
    │   ├─ Copilot                                        ✗ #161
    │   └─ Aider                                          ✗ #161
    ├─ → Edit ctx              → ctx CLI subprocess     ~ #160
    ├─ → Open with ▸ (submenu) → meld / vscode / etc.   ✗ #160
    └─ → Move to folder ▸      → folder selector        ✗ #160
```

## 3. Tabs — terminal lifecycle

```
■ Notebook tab strip
├─ ⊡ tab spawn (local / ssh / ai)                       ✓ partial (smoke_battery)
├─ ⊡ close button (×) per tab → on_tab_closed          ~ #157
├─ ⊡ tab title state — running / idle / error          ~ partial
└─ ⊡ tab switch — keyboard / click → focus restore     ✗ #157
```

## 4. Dialogs — modal flows

```
⊡ AISessionDialog (entry: File → New Claude Code, Sidebar → Add → Claude Code,
                   Sidebar → Run as)
├─ provider radio buttons (claude / copilot / aider)
├─ name entry — pin: must be unique (#108 BUG — open)
├─ folder entry
├─ project dir entry
├─ custom prompt entry
├─ flags (per provider): plan_mode (claude), grant_permissions (copilot), ...
└─ → Cancel / OK                                         ~ #160 (Add flow only)

⊡ SSHSessionDialog (entry: File → New SSH, Sidebar → Add → SSH)
├─ host / port / user / key path / auth method
└─ → Cancel / OK                                         ✗ #157, #160

⊡ OptionsDialog (entry: File → Options)
├─ Appearance (Theme / Font / Shell)
├─ AI integration (toggles, language)
├─ Panels visibility (5x toggle)
├─ Expander: AI Providers (lazy-built)                  ✓ #154
├─ Expander: Local Models (Ollama)                      ✓ #154 (Start/Stop/Refresh)
└─ → Cancel / Save                                       ✓ #154

⊡ InstallerWizard (entry: install.sh GTK auto-spawn, Tools → Install deps)
├─ Welcome page (radio Install/Fix/Uninstall + license)  ✓ tools/test_installer_wizard_vm.sh
├─ Picks page (deps checkboxes)                          ✓ tools/test_wizard_e2e_vm.sh
├─ Sudo / Progress / Summary                             ✓ tools/test_install_full_chain_vm.sh
└─ → Open BTerminal                                       ✓ tools/test_install_full_chain_vm.sh

⊡ DiagnosticsDialog                                      ~ #159
⊡ ErrataDialog                                           ~ #159
⊡ Updater modal                                          ~ #159
⊡ License dialog (first-run)                             ✓ test_license.py
```

## 5. Coverage map — task #156-#162 deliverables

| Task | Surface area | Driver | Live monitor | Screenshots | REST asserts |
|------|--------------|--------|--------------|-------------|--------------|
| #156 | (foundation) live screenshot framework | tools/_e2e_live_monitor.sh | self-test | per-2s grab | n/a |
| #157 | File menu — 5 items × spawn/dialog | tools/test_file_menu_vm.sh | yes | per-action | /api/tabs |
| #158 | View menu — 8 items × panel state | tools/test_view_menu_vm.sh | yes | per-toggle | /api/sidebar/state |
| #159 | Tools menu — 4 items × dialog content | tools/test_tools_menu_vm.sh | yes | per-dialog | /api/diagnostics |
| #160 | Sidebar CRUD — 5 actions × 3 row types | tools/test_sidebar_crud_vm.sh | yes | per-CRUD-step | /api/sessions |
| #161 | AI session spawn — 3 providers × spawn+prompt | tools/test_ai_spawn_vm.sh | yes | tab+banner+output | /api/tabs/ai/{provider}, /api/tabs/{i}/feed |
| #162 | Installer extra scenarios — 5 edge cases | tools/test_installer_edge_vm.sh | yes | per-scenario | install.log markers |

## 6. Common helpers (factor across #157-#162)

- `_e2e_focus_window` — xdotool `windowactivate --sync` + verify `getactivewindow`
- `_e2e_screenshot $name` — gnome-screenshot → `smoke-logs/<task>/<name>.png`
- `_e2e_assert_window_title $regex` — xdotool getwindowname
- `_e2e_send_keys $keys` — plain `xdotool key` (NOT `--window`; XSendEvent ignored by GTK)
- `_e2e_wait_for_log_marker $log $marker` — tail -f with timeout
- `_e2e_rest_get $path` / `_e2e_rest_post $path $body` — debug-REST against :7780

## 7. Acceptance criteria for #155 closure

1. ✅ Doc updated z control-flow graph (this section)
2. ✅ Tasks #156-#162 already enqueued with explicit per-flow scope
3. ✅ Helper plan documented (section 6) so #156 doesn't reinvent for each menu
4. ✅ Coverage table identifies which surface ties to which task
5. ✅ Existing coverage marked (✓ AISessionDialog test_options_dialog #154,
   InstallerWizard 4× existing scripts) so we don't double-cover

**Następny etap:** #156 dostarcza framework, potem #157-#162 chodzą sekwencyjnie
przez every menu/sidebar/dialog. Plus #163-#179 — manual QA per Release QA
methodology #164.
