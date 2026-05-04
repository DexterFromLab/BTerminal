# BTerminal — Test Coverage Matrix

**Źródło:** REQUIREMENTS.md (66 wymagań top-level, ~200+ sub-criteria)
**Test suite:** 24 plików `tests/test_*.py` + `tests/action_graph/`
**Data:** 2026-05-04
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
| ✅ Covered | 11 | 17% |
| ⚠️ Partial | 14 | 21% |
| ❌ Missing | 28 | 42% |
| 🟦 N/A | 2 | 3% |
| 🔴 Not implemented | 11 | 17% |
| **TOTAL** | **66** | 100% |

**Wniosek:** ~38% pokrycia (covered + partial). Big gaps: UI flows
(panele, dialogi, theme), end-to-end task auto-trigger, intro prompt
structure, CLI tools (ctx/tasks/consult/memory_wizard/claude_log).
Provider abstraction (R4/R10) nie zaimplementowana = brak testów.

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
| R4.1-3 ai_sessions.json | 🔴❌ | — | NIE ZAIMPLEMENTOWANE (claude_sessions.json wciąż) |
| R4a provider capabilities | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
| R4b migration | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
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
| R8.1 binary path search | ❌ | — | brak testu _find_claude_path candidates |
| R8.2 argv composition | ❌ | — | brak testu --resume/--skip-permissions flagi |
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
| R10 provider abstraction | 🔴❌ | — | NIE ZAIMPLEMENTOWANE |
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

## §12. Stats bar [Claude-only]

| R | Status | Test | Gap |
|---|--------|------|-----|
| R31.1-3 stats display | ❌ | — | UI panel nie testowany |
| R32.1-4 session log reader | ❌ | — | brak testu _SessionStatsReader |
| R33.1-3 cost calculation | ❌ | — | brak unit testu _STATS_PRICING |
| R34.1-5 plan usage API | ❌ | — | brak mock'u API + reader test |

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
