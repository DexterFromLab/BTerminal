# BTerminal — Requirements Specification

**Status:** wstępna wersja, do dyskusji.
**Data:** 2026-05-04
**Cel:** skodyfikować zachowanie systemu jako kontrakt → punkt wyjścia dla
testów regresji + abstrakcji multi-CLI.

**Konwencja:**
- `R<N>` — top-level requirement
- `R<N>.<n>` — sub-requirement (krok / asercja)
- `R<N>.f<n>` — failure mode (jak system reaguje na błąd)
- `Q:` — **open question** — moje rozumienie wymaga potwierdzenia
- `[Claude-only]` — obecnie hardcoded pod Claude, do uogólnienia gdy
  dochodzą Copilot/Aider
- `[provider-agnostic]` — działa niezależnie od konkretnego AI CLI

---

## Spis treści

1. [Application lifecycle](#1-application-lifecycle)
2. [Session management](#2-session-management)
3. [Terminal tab lifecycle](#3-terminal-tab-lifecycle)
4. [AI CLI integration](#4-ai-cli-integration)
5. [Intro prompt builder](#5-intro-prompt-builder)
6. [Rules injection](#6-rules-injection)
7. [Ctx subsystem](#7-ctx-subsystem)
8. [Tasks subsystem (auto-trigger)](#8-tasks-subsystem-auto-trigger)
9. [Memory (rules) subsystem](#9-memory-rules-subsystem)
10. [In-process plugin contract](#10-in-process-plugin-contract)
11. [Sidecar plugin contract](#11-sidecar-plugin-contract)
12. [Stats bar](#12-stats-bar)
13. [Skills panel](#13-skills-panel)
14. [Files panel](#14-files-panel)
15. [Git panel](#15-git-panel)
16. [Consult panel](#16-consult-panel)
17. [Theme system](#17-theme-system)
18. [Auto-update + errata](#18-auto-update--errata)
19. [Debug REST API](#19-debug-rest-api)
20. [Configuration persistence](#20-configuration-persistence)
21. [Installer](#21-installer)
22. [CLI tools](#22-cli-tools)

---

## 1. Application lifecycle

### R1: Boot sequence
**Trigger:** `python -m bterminal` lub `bterminal-launcher`

**Behavior:**
- R1.1 — argparse parsuje znane flagi i **błędem zatrzymuje się przy
  nieznanych** (`parser.parse_args` zamiast `parse_known_args`).
  Komunikat: `bterminal: error: unrecognized arguments: <flag>` +
  exit code 2. *(decyzja: 2026-05-04)*
- R1.2 — `--debug-rest` flag jest jedynym sposobem włączenia debug REST.
  `BTERMINAL_DEBUG_REST` env var **nie jest obsługiwany** —
  uproszczenie surface'u CLI. *(decyzja: 2026-05-04)*
- R1.3 — `os.environ["PATH"]` jest prefixowane `~/.local/bin` i
  `~/.npm-global/bin` zanim BTerminal odpali jakiekolwiek
  `subprocess.run(["ctx", ...])`, `["claude", ...]`, itd.
  **Powód:** gdy BTerminal jest uruchamiany z desktop entry / file
  managera, dziedziczone PATH jest minimalne (`/usr/local/bin:/usr/bin:/bin`)
  i NIE zawiera `~/.local/bin` ani `~/.npm-global/bin`. Bez tego prefixu
  każde wywołanie `subprocess.run(["ctx", ...])` rzuca
  `FileNotFoundError` widoczne w GUI jako "Nie ma takiego pliku".
- R1.4 — `Gtk.Application` z `flags=NON_UNIQUE` — wiele instancji bterminal
  może działać równolegle. *(potwierdzone: Q1.1 zamknięte)*
- R1.5 — `BTerminalApp` window pojawia się na ekranie max ~3s od startu
- R1.6 — `_check_for_updates(window, manual=False)` odpala się 3s po
  starcie *wyłącznie* gdy `_OPTIONS["check_updates_on_start"] == True`

**Failure modes:**
- R1.f1 — brak `~/.config/bterminal/options.json` → defaults z
  `_OPTIONS_DEFAULTS`, `_save_options` tworzy plik z defaults
- R1.f2 — uszkodzony JSON w options.json → komunikat o błędzie
  (`show_error_dialog`: "Plik options.json był uszkodzony — przywrócono
  domyślne ustawienia") + nadpisanie pliku domyślnymi opcjami
  (self-healing). Bez crashu, bez utraty kolejnego startu. *(decyzja: 2026-05-04)*
- R1.f3 — brak DISPLAY → GTK error, exit z kodem ≠0 (nie obsługujemy
  headless mode, oprócz testów pod xvfb)

**Existing tests:** `test_health.py`, `test_gtk_alongside_sidecars.py`

---

### R2: Clean shutdown
**Trigger:** użytkownik zamyka okno / `POST /api/quit?confirm=true` /
SIGTERM

**Behavior:**
- R2.1 — wszystkie sidecary tracked przez `SidecarRunner` → SIGTERM,
  potem SIGKILL po 5s timeout (atexit hook)
- R2.2 — debug REST server idempotent shutdown (server.shutdown() +
  server_close())
- R2.3 — wszystkie dzieci VTE (claude / ssh / shell) zabite — brak
  zombie procesów
- R2.4 — `_collect_claude_log` zachowuje JSONL session do
  `<project>/claude_log/` na zamknięcie tabu Claude

**Q2.1:** czy chcemy potwierdzenie zamknięcia gdy są aktywne taby
Claude / sidecary? Obecnie "Cmd+Q" zamyka bez pytania.

**Q2.2:** sidecary z `start_new_session=True` przeżywają crash BTerminal.
Czy to celowe? (Tak, zgodnie z komentarzem w SidecarRunner — atexit jest
"canonical shutdown path".)

---

## 2. Session management

### R3: SSH session storage
**Trigger:** użytkownik dodaje sesję SSH przez `SessionDialog`

**Behavior:**
- R3.1 — sesja zapisana w `~/.config/bterminal/sessions.json` (atomic
  write via tempfile + os.replace)
- R3.2 — wymagane pola: `host` (`SessionManager.validate_entry`)
- R3.3 — opcjonalne pola: `name`, `user`, `port`, `key_path`, `password`,
  `color`, `folder`, `macros[]`
- R3.4 — `id` autogenerowane (UUID v4) na `add()`
- R3.5 — sesje renderowane w sidebarze posortowane wg user-defined
  kolejności (drag & drop reorder)

**Failure modes:**
- R3.f1 — brak `host` → `ValueError("SSH session requires 'host'")`
- R3.f2 — uszkodzony sessions.json → `self.sessions = []`, brak crashu

### R3a: SSH session passwords — IN-MEMORY ONLY *(decyzja: 2026-05-04)*

**Wymaganie:** hasła SSH **nigdy nie są zapisywane na dysku**. Cache
tylko w pamięci procesu BTerminal, traci się przy restarcie aplikacji.

**Behavior (do zaimplementowania):**
- R3a.1 — `SessionManager.save()` filtruje pole `password` przed
  zapisem do sessions.json (drop)
- R3a.2 — `SessionManager.load()` ignoruje pole `password` jeśli
  istnieje w pliku (np. z legacy installacji)
- R3a.3 — Nowy `SessionPasswordCache` (in-memory dict keyed by `id`):
  - `set(session_id, password)` — zapisuje w RAM
  - `get(session_id)` → password lub None
  - `pop(session_id)` — czyści po zamknięciu sesji (opcjonalnie)
- R3a.4 — `SessionDialog` przy save'ie sesji z hasłem: tylko
  `SessionPasswordCache.set(...)`, **bez przekazywania do `manager.add()`**
- R3a.5 — Spawn SSH: `SessionPasswordCache.get(id)`. Jeśli None →
  prompt dialog "Hasło dla {host}: ___ [✓ Pamiętaj na czas tej sesji]"
- R3a.6 — Migracja: pierwszy load z `password` w sessions.json → drop
  pole + log info ("Migrated: passwords removed from sessions.json")

**Status:** **NIE ZAIMPLEMENTOWANE** — do refactoringu w osobnym etapie.

**Powód:** plain-text passwords w JSON na dysku to ryzyko bezpieczeństwa
(file dump, backup, sync do gita itp.). In-memory tylko = ryzyko
ograniczone do żywego procesu.

---

### R4: AI CLI session storage *(uogólnione: 2026-05-04)*

**Decyzja:** jeden uniwersalny manager dla wszystkich AI CLI
(Claude, Copilot, Aider, future). Każda sesja ma pole `provider:` które
wybiera implementację z listy zarejestrowanych providerów. Logika
wspólna domyślnie aktywna; pojedyncze cechy wyłącza się przez
**capability flags** w konfiguracji providera.

**Behavior (docelowe):**
- R4.1 — config zapisany w `~/.config/bterminal/ai_sessions.json`
  (rename z `claude_sessions.json`)
- R4.2 — schema sesji (provider-agnostic core + provider-specific extras):
  ```json
  {
    "id": "<uuid>",
    "name": "string",
    "provider": "claude" | "copilot" | "aider" | ...,
    "project_dir": "/path",
    "prompt": "string",                          // custom intro append
    "color": "#hex" | null,
    "enabled_plugins": ["plugin1", ...] | null,
    "provider_options": {                        // provider-specific
        "resume": true,                          // claude only
        "skip_permissions": true,                // claude only
        ...
    }
  }
  ```
- R4.3 — `enabled_plugins=null` = wszystkie globalnie enabled plugins
  aktywne (backwards-compat); `[]` = żaden plugin

### R4a: Provider capabilities config *(decyzja: 2026-05-04)*

**Plik:** `~/.config/bterminal/providers.json` (lub `defaults/providers.json`
jako bundled defaults)

**Schema:**
```json
{
  "providers": {
    "claude": {
      "binary_search_paths": ["~/.local/bin/claude", ...],
      "capabilities": {
        "intro_prompt":       true,    // BTerminal feeds intro via stdin
        "resume_flag":        true,    // supports --resume
        "skip_permissions":   true,    // supports --dangerously-skip-permissions
        "session_log":        true,    // produces JSONL session log
        "session_log_path":   "~/.claude/projects/{sanitized_project}/*.jsonl",
        "usage_api":          true,    // has plan-usage API endpoint
        "usage_api_url":      "https://api.anthropic.com/api/oauth/usage",
        "rules_inject":       true,    // supports periodic rules re-injection
        "task_auto_trigger":  true,    // works with auto-trigger flow
        "stats_bar":          true     // stats bar shows tokens/cost
      },
      "pricing": { "claude-opus-4-6": {...}, ... }
    },
    "copilot": {
      "binary_search_paths": ["/usr/bin/gh"],
      "argv_prefix": ["copilot"],
      "capabilities": {
        "intro_prompt":       false,   // copilot doesn't take stdin prompt
        "resume_flag":        false,
        "session_log":        false,
        "usage_api":          false,
        "rules_inject":       false,
        "task_auto_trigger":  false,
        "stats_bar":          false
      }
    },
    "aider": {
      "binary_search_paths": ["~/.local/bin/aider"],
      "capabilities": {
        "intro_prompt":       true,    // aider accepts initial prompt
        "session_log":        false,   // aider .aider.chat.history.md format — TBD
        "rules_inject":       true,    // via stdin
        "stats_bar":          false    // aider doesn't expose tokens easily
      }
    }
  }
}
```

**Behavior:**
- R4a.1 — Każda komponenta BTerminal (intro builder, stats bar,
  task auto-trigger, rules inject) sprawdza
  `provider.capabilities[<feature>]` i pomija się jeśli `false`.
- R4a.2 — Stats bar: gdy `capabilities.stats_bar == false` → bar
  nie pojawia się na tabie tego providera.
- R4a.3 — Auto-trigger: gdy `capabilities.task_auto_trigger == false`
  → `_on_task_idle_timeout` zwraca False bez próby fired.
- R4a.4 — Intro prompt: gdy `capabilities.intro_prompt == false` →
  pomija feed intro, spawn binary bez stdin pre-write.

### R4b: Migracja `claude_sessions.json` → `ai_sessions.json` *(2026-05-04)*

**Behavior (one-shot, na pierwszy start po update):**
- R4b.1 — Jeśli `ai_sessions.json` nie istnieje, ALE
  `claude_sessions.json` istnieje:
  - czytaj claude_sessions.json
  - dla każdej sesji: dodaj `provider: "claude"`, przenieś
    `resume`/`skip_permissions` do `provider_options`
  - zapisz jako `ai_sessions.json`
  - zostaw oryginał `claude_sessions.json` (backup) z suffixem `.bak`
- R4b.2 — Po migracji: BTerminal czyta tylko `ai_sessions.json`.
- R4b.3 — Migracja idempotent — kolejne uruchomienia bez efektu jeśli
  `ai_sessions.json` istnieje.

**Status:** **NIE ZAIMPLEMENTOWANE** — provider abstraction +
migration to refactor w osobnym etapie.

---

### R5: Macros for SSH sessions
**Trigger:** prawym na sesji SSH → "Add Macro"

**Behavior:**
- R5.1 — macro = sekwencja kroków (text + opcjonalny delay w ms)
- R5.2 — uruchomiony przez prawym → "Run macro" feeds kroki do
  aktywnego tabu z odpowiednimi delayami między krokami
- R5.3 — macro storage: pod kluczem `macros[]` w danej sesji
- R5.4 — wsparcie tylko dla **delay** między krokami; brak "wait for
  prompt regex". *(decyzja: 2026-05-04)*

---

### R6: Folder organization
**Trigger:** prawym na sesji → "Move to folder"

**Behavior:**
- R6.1 — folder = string field `folder` na sesji
- R6.2 — sidebar grupuje sesje wg folder (rendering hierarchy)
- R6.3 — "Ungroup folder" usuwa folder ze wszystkich sesji w nim

---

## 3. Terminal tab lifecycle

### R7: Tab creation [provider-agnostic]
**Trigger:** double-click sesji / "+" tab / REST `POST /api/tabs/{type}`

**Behavior:**
- R7.1 — nowy `TerminalTab` widget dodany do `BTerminalApp.notebook`
- R7.2 — `Vte.Terminal` wewnątrz, z palette + font z config
- R7.3 — tab title = decorated name (visual provider marker + session
  name + index), kolor zakładki = `_session_color(type)` lub custom
- R7.4 — close button (×) na tabie
- R7.5 — `notebook.set_tab_reorderable(tab, True)` — tab może być
  przeciągnięty (dotyczy także AI CLI tabs)
- R7.6 — gdy `provider.capabilities.stats_bar == true`: `_stats_bar`
  pojawia się na dole tabu

### R7a: Visual provider marker on tab *(decyzja: 2026-05-04)*

**Wymaganie:** tab musi wizualnie sygnalizować jaki AI CLI jest w nim
uruchomiony. SSH/local też mają własny marker. Marker pomaga przy
wielu otwartych tabach z różnymi providerami.

**Behavior (do zaimplementowania):**
- R7a.1 — Każdy provider ma `display.icon` (emoji lub ikona GTK) +
  `display.short_label` w `providers.json`:
  ```json
  "claude":  { "display": { "icon": "✨", "short_label": "Claude",  "color": "#89b4fa" } },
  "copilot": { "display": { "icon": "🤖", "short_label": "Copilot", "color": "#a6e3a1" } },
  "aider":   { "display": { "icon": "🛠", "short_label": "Aider",   "color": "#f9e2af" } },
  ```
- R7a.2 — Tab label format: `{icon} {session_name}` (np. `✨ moj-projekt`)
- R7a.3 — SSH/local mają wbudowane ikony: `🔐` SSH, `💻` local
- R7a.4 — Tooltip na zakładce: `{short_label}: {full session name}`
- R7a.5 — Kolor podkreślenia zakładki = `provider.display.color`
  (override'uje session.color jeśli session.color brak)

**Q7a.1:** Czy chcemy emoji czy własne ikony GTK (z `Gtk.Image`)? Emoji
prostsze (string w label), ikony SVG ładniejsze ale wymagają assets.

**Q7a.2:** Co gdy provider nie ma `display.icon` w configu (np. nowy
provider, brak metadanych)? Default `?` lub generic `🤖`?

---

### R8: Claude tab spawn [Claude-only → uogólnić jako AI CLI tab]
**Trigger:** otwarcie tabu z `claude_config != None`

**Behavior:**
- R8.1 — `_find_claude_path()` zwraca pierwszy istniejący z:
  `~/.local/bin/claude`, `~/.npm-global/bin/claude`,
  `/usr/local/bin/claude`, `/usr/bin/claude`,
  `/opt/homebrew/bin/claude`, `~/.nvm/versions/node/*/bin/claude`,
  PATH lookup
- R8.2 — argv: `[claude_path, "--resume"?, "--dangerously-skip-permissions"?]`
- R8.3 — feeds intro prompt jako pierwsza wartość stdin (po starcie subprocess)
- R8.4 — gdy claude exits, shell zostaje (`exec bash` fallback) — tab
  nie zamyka się sam
- R8.5 — `claude_log_dir(project_dir).mkdir(exist_ok=True)` — log dir
  utworzony przed startem

**Failure modes:**
- R8.f1 — `_find_claude_path() == None` → terminal pokazuje formatted
  error message ze ścieżkami szukanymi (printf z ANSI red)
- R8.f2 — `project_dir` nie istnieje → modal dialog z komunikatem:
  "Katalog projektu '{path}' nie istnieje. Utworzyć?" z opcjami
  [Utwórz] [Anuluj]. *(decyzja: 2026-05-04)*
  - [Utwórz] → `Path(project_dir).mkdir(parents=True, exist_ok=True)`,
    spawn kontynuowany
  - [Anuluj] → tab nie jest otwierany, brak side-effectów
  - Brak zapisu do session config (path zostaje jak był — user może
    poprawić w Edit dialog)

---

### R8a: AI CLI runtime errors [provider-agnostic] *(decyzja: 2026-05-04)*

**Wymaganie:** błędy zwracane przez subprocess (claude/copilot/aider)
są **traktowane jako output terminala**, BTerminal nie wyświetla
modal alertów ani nie filtruje stderr.

**Behavior:**
- R8a.1 — stderr subprocessu jest forwardowany do VTE jak normalny output
- R8a.2 — Brak interpretacji błędów po stronie BTerminal — to provider
  ma odpowiedzialność komunikować błąd użytkownikowi w terminalu
- R8a.3 — Wyjątek: gdy subprocess **w ogóle nie wystartuje** (binary
  not found) — wtedy R8.f1 (formatted error w VTE) bo BTerminal nie ma
  innej drogi pokazania błędu

### R9: Tab close [provider-agnostic]
**Trigger:** × button / Cmd+W / `POST /api/tabs/{idx}/close`

**Behavior:**
- R9.1 — child process (claude/ssh/bash) → SIGTERM
- R9.2 — `_collect_claude_log(tab)` jeśli claude tab
- R9.3 — `_sidecar_release(name)` dla każdego sidecara w
  `tab.enabled_plugins` (refcount--)
- R9.4 — VTE widget destroy
- R9.5 — `notebook.remove_page(idx)` + tab title aktualizacja innych
  zakładek (renumeracja)

**Failure modes:**
- R9.f1 — `tab._task_project` set + autorun aktywny → REST `/close`
  zwraca 409 chyba że `?force=true`
- R9.f2 — GUI close button na tabie z aktywnym auto-trigger →
  potwierdzenie modalne "Tab '{name}' ma aktywny auto-trigger
  zadania '{task_id}'. Zamknąć mimo to?" [Zamknij] [Anuluj].
  *(decyzja: 2026-05-04 — symetria z REST 409)*

---

## 4. AI CLI integration

### R10: AI CLI provider abstraction [docelowo]
**Trigger:** N/A — strukturalne wymaganie

**Behavior (docelowo, gdy refactor zrobiony):**
- R10.1 — `AICLIProvider` ABC z metodami `binary_path`, `build_argv`,
  `build_intro_prompt`, `session_log_path`, `parse_session_stats`,
  `usage_api`
- R10.2 — `ClaudeProvider`, `CopilotProvider`, `AiderProvider`
  implementują ABC
- R10.3 — `BTerminalApp` ma `self.providers: dict[str, AICLIProvider]`
- R10.4 — `claude_config["provider"]` = klucz do providera

**Status:** **NIE ZAIMPLEMENTOWANE** — do refactoringu.

---

## 5. Intro prompt builder

### R11: Intro prompt structure [Claude-only → uogólnić]
**Trigger:** spawn nowego Claude tabu

**Behavior (`_compute_intro_prompt_for_tab`):**
- R11.1 — Format markdown sekcjami `##`
- R11.2 — Sekcje (kolejność):
  1. Custom prompt z config'u (jeśli jest)
  2. Header BTerminal — kontekst projektu (project_dir, ctx output)
  3. Per-plugin section — dla każdego enabled plugin:
     `plugin.get_session_context()` → string
  4. Per-sidecar section — dla każdego enabled sidecara:
     manifest.prompt sformatowany jako sekcja
- R11.3 — Sekcje rozdzielone `\n\n`
- R11.4 — `enabled_plugins=None` → wszystkie domyślnie enabled (z
  default_in_session=True)

**Q11.1:** Custom prompt vs ctx output — co jest pierwsze? Obecnie:
ctx → custom. Czy może odwrotnie (custom override → ctx supplement)?

**Q11.2:** Provider-agnostic intro prompt format vs Claude-specific
markdown — czy każdy provider będzie miał własny format
(`provider.intro_prompt_format`)?

---

### R12: ctx output in intro [provider-agnostic]
**Trigger:** project_dir set + ctx project zarejestrowany

**Behavior:**
- R12.1 — `_fetch_ctx_output(project_name)` = `subprocess.run(["ctx",
  "get", project_name, "--shared"])` stdout
- R12.2 — Wynik wstawiony jako pierwsza sekcja po custom prompt
- R12.3 — Jeśli `ctx` CLI niedostępny → ctx output pominięty (silent
  fallback)

---

### R13: Tools help block [provider-agnostic]
**Trigger:** intro prompt builder

**Behavior:**
- R13.1 — `_tools_help(project_name)` zwraca string opisujący dostępne
  CLI tools (ctx, consult, tasks, memory_wizard, claude_log)
- R13.2 — Wstawiany w sekcji "## Narzędzia"

**Q13.1:** Czy tools help powinien być provider-agnostic czy każdy
provider definiuje własny? (Niektóre CLI mogą mieć inne nazwy tooli.)

---

## 6. Rules injection

### R14: Rules block initial inject
**Trigger:** spawn Claude tab z `rules` skonfigurowanymi w memory

**Behavior:**
- R14.1 — `_read_global_rules()` + `_fetch_rules_block(project)` =
  global + per-project rules → string
- R14.2 — Wstawione do intro prompt jako jedna sekcja
- R14.3 — Format: nagłówek "## Rules" + numbered list

---

### R15: Rules periodic re-injection
**Trigger:** Claude wysyła N-ty prompt (count == inject_every)

**Behavior:**
- R15.1 — `inject_every` z `rules_config` (default 100)
- R15.2 — `_maybe_inject_rules` ustawia `_inject_pending = (project,
  count, refresh_every)` na boundary
- R15.3 — `_on_task_idle_timeout` (10s idle) wykonuje injection przed
  next task
- R15.4 — Re-injected = pure rules block (bez header / global / tools
  help — żeby nie spamować)
- R15.5 — Co `refresh_every` (default 200) — dodatkowa wiadomość ctx
  refresh wysłana JAKO OSOBNA wiadomość po krótkim delayu

**Q15.1:** Co dzieje się gdy user zamknie tab przed flush'em pending
inject? (Obecnie pending jest droppedy z TerminalTab.) OK?

**Q15.2:** Re-injection assumes Claude jest idle — co jeśli Claude
długo myśli (>10s) podczas crossing inject_every boundary? Inject
może się odpalić w środku odpowiedzi.

---

## 7. Ctx subsystem

### R16: Ctx project resolution
**Trigger:** każda operacja ctx-related dla `project_dir`

**Behavior:**
- R16.1 — `_resolve_ctx_project_name(project_dir)` najpierw szuka w
  sessions table (RTRIM matching path + walk up)
- R16.2 — Fallback: `_smart_project_name(dir)` — basename dir, ALE
  jeśli basename ∈ `_GENERIC_SUBDIRS` (src, docs, lib, ...) → walk
  up do .git → użyj tej nazwy
- R16.3 — Jeśli sessions niedostępna (brak DB) → tylko smart name

---

### R17: Ctx wizard (new project setup)
**Trigger:** `ClaudeCodeDialog` save z project_dir, ctx CLI dostępny,
project nie zarejestrowany

**Behavior:**
- R17.1 — `CtxSetupWizard` — 4 kroki:
  1. Intro
  2. Project description (auto-detect od `_detect_project_description`)
  3. Claude integration (CLAUDE.md create/update)
  4. Done — zapis do ctx DB
- R17.2 — Tworzy CLAUDE.md jeśli nie istnieje
- R17.3 — Inicjalizuje sessions + ctx_entries

**Q17.1:** Czy wizard powinien być **opcjonalny** (Skip)? Obecnie
zawsze pojawia się gdy spełnione warunki.

---

### R18: Ctx CRUD via panel
**Trigger:** `CtxManagerPanel` w sidebarze

**Behavior:**
- R18.1 — Tree: project → category → entries
- R18.2 — Add/Edit/Delete via `_CtxEntryDialog`
- R18.3 — Refresh button → reload from DB (pickup external `ctx set`
  z CLI)
- R18.4 — Image attachments via clipboard paste / file dialog

---

### R19: Ctx export/import
**Trigger:** `_CtxExportDialog` / `_CtxImportDialog`

**Behavior:**
- R19.1 — Export: project's entries → JSON file (zawiera images jako
  base64)
- R19.2 — Import: JSON → DB merge (default) lub overwrite (flag)
- R19.3 — Import shared entries explicite excluded chyba że flag
  `--include-shared`

**Q19.1:** Round-trip integrity — export → import → assert identical
content. Confirmed czy do testowania?

---

## 8. Tasks subsystem (auto-trigger)

### R20: Task data model
**Schema (sqlite, ctx DB):**
- R20.1 — `tasks` table: `id`, `project`, `task_id` (string, hierarchical
  jak "1", "1.a", "1.b", "2"), `description`, `status` (open/done),
  `created_at`, `updated_at`, UNIQUE(project, task_id)
- R20.2 — `task_config` table: `project` PK, `autorun` int (0/1)
- R20.3 — `task_claims` table: `(project, task_id)` PK, `session_id`,
  `claimed_at`

---

### R21: Task auto-trigger lifecycle [PROVIDER-AGNOSTIC, ALE Claude-coupled]
**Trigger:** user kliknie "Start" w `TaskListPanel` przy wybranym
projekcie

**Behavior:**
- R21.1 — `task_config(project).autorun = 1` zapisany do DB
- R21.2 — `_trigger_first_task(project)` → znajduje matching tab
  (`tab._task_project == project`)
- R21.3 — `TerminalTab._claim_next_task(db, project, session_id)`
  atomically claims pierwszy unclaimed open task
- R21.4 — Feed message do terminala:
  ```
  [AUTO-TRIGGER] Twoje przypisane zadanie: {id} — {desc}
  Sprawdź pełną listę: tasks context {project} --session {session}
  MUSISZ oznaczyć po wykonaniu: tasks done {project} {id} (w Bash). ...
  ```
- R21.5 — `feed_child(b"\r")` po 100ms (GLib.timeout_add) — Enter
- R21.6 — Po 10s ciszy w terminalu: `_on_task_idle_timeout` →
  jeśli `task_config.autorun == 1` → next claim → next message
- R21.7 — Pętla kończy się gdy `_claim_next_task` zwróci None (brak
  unclaimed open tasks)

**Failure modes:**
- R21.f1 — `tab._task_project == None` → tab nie partycypuje (dropdown
  nie wybrany)
- R21.f2 — `autorun == 0` → no-op (`_on_task_idle_timeout` returns False)
- R21.f3 — Restart BTerminal → `_reset_all_autorun()` w `TaskListPanel.__init__`
  zeruje wszystkie autoruny ([Q21.1] surprising — bug?)

**Q21.1:** Reset autorun przy restarcie aplikacji to celowe? Bug do
naprawy? (Komentarz w kodzie mówi "always OFF on startup" — celowe.)

**Q21.2:** Co gdy DWIE Claude sesje mają ten sam `_task_project` —
race? Atomic claim teoretycznie ich rozdzieli (różne session_id), ale
race nadal możliwy w trakcie auto-trigger.

**Q21.3:** Provider-agnostic? Auto-trigger działa dla każdego AI CLI
czy tylko Claude? Obecnie wymaga `tab._stats_bar` + `claude_config`.

---

### R22: Task editing via panel
**Trigger:** `TaskListPanel` actions

**Behavior:**
- R22.1 — Project dropdown — zmiana zapisuje `tab._task_project`
- R22.2 — Sort: `_task_sort_key` natural sort (1, 1.a, 1.b, 2, 10)
- R22.3 — Active vs Done sections (dwa list stores)
- R22.4 — `tasks` CLI tool dostępny dla user'a + Claude

---

## 9. Memory (rules) subsystem

### R23: Per-project rules
**Storage:** `~/.config/bterminal/memory/<project>/`

**Behavior:**
- R23.1 — `MemoryPanel` listuje projekty + ich reguły
- R23.2 — `inject_every` + `refresh_every` per-project (rules_config
  table w ctx DB)
- R23.3 — Logi z minionych sesji widoczne (`_claude_log_dir(project_dir)`
  zwraca `<project>/claude_log/` lub `<project>/.claude_log/` fallback)

### R23a: Log retention *(decyzja: 2026-05-04)*

**Wymaganie:** retencja JSONL session logs w `<project>/claude_log/`:
**ostatnie 20 rozmów per projekt**, starsze automatycznie usuwane.

**Behavior:**
- R23a.1 — Trigger rotacji: po `_collect_claude_log(tab)` na zamknięcie
  tabu Claude (lub okresowo przy starcie BTerminala)
- R23a.2 — `glob('<project>/claude_log/*.jsonl')` posortowane wg mtime,
  najnowsze 20 zostawione, reszta `os.remove()`
- R23a.3 — Default: 20. Override: `_OPTIONS["claude_log_retention"]`
  (int, opcjonalny — przyszły feature, nie wymagany teraz)
- R23a.4 — Cleanup nie pyta usera, jest cichy (logged do stderr)

**Q23a.1:** czy 20 jest globalnym defaultem czy per-project'em? Obecnie
proponuję per-project (każdy projekt ma swoje 20 rozmów).

---

### R24: Memory wizard
**Trigger:** `memory_wizard <project> --project-dir <dir> [--dry-run]`

**Behavior:**
- R24.1 — Czyta logi sesji Claude z `~/.claude/projects/.../*.jsonl`
- R24.2 — Detektuje wzorce poprawek użytkownika
- R24.3 — Proponuje reguły do zapisania
- R24.4 — `--dry-run` = tylko sugeruj, nie zapisuj

---

## 10. In-process plugin contract

### R25: Plugin loader
**Storage:** `~/.config/bterminal/plugins/<name>.py` lub `<name>/`

**Behavior:**
- R25.1 — `BTerminalApp._load_plugins()` skanuje katalog na boot
- R25.2 — Importuje przez `importlib.spec_from_file_location`
- R25.3 — Wywołuje `module.create_plugin(app)` → instance
- R25.4 — `_register_plugin(plugin)` — wywołuje `plugin.activate(app)`,
  dodaje panel do sidebar_stack, rejestruje shortcut'y
- R25.5 — `~/.config/bterminal/plugins.json` — enable/disable map

**Failure modes:**
- R25.f1 — Plugin file raises podczas import → log + continue (inne
  plugins ładują się)
- R25.f2 — `module.create_plugin(app)` raises → log + continue

---

### R26: Plugin contract
- R26.1 — `BTerminalPlugin` klasa bazowa (lub duck-typed)
- R26.2 — Required: `name`, `title`, `version`, `description`, `author`
- R26.3 — Lifecycle: `activate(app) → Gtk.Widget|None`, `deactivate()`,
  `get_keyboard_shortcuts() → list[tuple[mod, keyval, callback]]`,
  `on_sidebar_shown()`, `get_session_context() → str|None`
- R26.4 — `default_in_session: bool` — czy plugin auto-enabled w
  nowych Claude tabs

---

### R27: Hot toggle
**Trigger:** `PluginManagerPanel` toggle / REST `POST /api/plugins/{n}/{enable|disable}`

**Behavior:**
- R27.1 — `_hot_load_plugin(name)` — load + register bez restart
- R27.2 — `_hot_unload_plugin(name)` — deactivate + remove from sidebar
- R27.3 — `plugins.json` updated atomically

### R27a: Globalny disable a aktywne taby *(decyzja: 2026-05-04)*

**Wymaganie:** disable globalny pluginu nie wpływa na taby które już
go używają. Zmiana dotyczy **przyszłych** tabów.

**Behavior:**
- R27a.1 — Globalny `_hot_unload_plugin(name)`:
  - usuwa plugin'a panel z sidebar_stack
  - **NIE** czyści `tab.enabled_plugins` w aktywnych tabach
  - aktywne taby tracą widoczność panelu, ale `get_session_context()`
    dalej wpływa na intro prompt jeśli plugin obiekt jeszcze żyje
- R27a.2 — Refcount sidecarów: globalny disable **nie zwalnia** istniejących
  acquire'ów (sidecary trzymane przez aktywne taby zostają running)
- R27a.3 — Nowe taby nie mogą enable'ować zdisabled pluginu
  (PluginManagerPanel pokazuje go z flagą "disabled")

**Powód:** disable mid-session crashowałby UI tabów które aktywnie używają
panelu. Symantyka "next session" jest przewidywalna i bezpieczna.

---

## 11. Sidecar plugin contract

### R28: Sidecar manifest
**Storage:** `~/.config/bterminal/sidecars/<name>.json`

**Schema:**
- R28.1 — Required: `name`
- R28.2 — Optional: `plugin_address`, `plugin_dashboard`,
  `healthcheck_url`, `run_command`, `reset_command`, `cwd`, `env`,
  `prompt`, `title`, `description`, `default_in_session`, `auto_start`
- R28.3 — `from_dict` drops unknown keys (forward-compat)

---

### R28a: `auto_start` flag — task-related sidecars *(2026-05-04)*

**Interpretacja (mojej rekonstrukcji, do potwierdzenia):**
flaga `auto_start: true` w manifest oznacza sidecar **wcześniej
startowany dla wsparcia narzędzia `tasks`** — np. `taskboard` sidecar
musi być up gdy autorun zadań jest aktywny dla projektu, niezależnie
czy jakiś tab go acquired przez refcount.

**Behavior (proponowane):**
- R28a.1 — Sidecar z `auto_start: true` startuje gdy:
  - autorun zadań włączony dla **dowolnego** projektu, LUB
  - alternatywa: BTerminal boot (eager start) — TBD
- R28a.2 — Stop'uje gdy autorun wyłączony dla wszystkich projektów
- R28a.3 — Refcount NIE wpływa na auto-start sidecar (gdy `auto_start: true`,
  refcount jest "pseudo-1" przez cały czas gdy autorun aktywny)

**Q28a.1:** czy interpretacja "auto_start = task-bound" jest poprawna?
Czy chcesz innej semantyki (np. "auto_start = boot z BTerminalem")?

### R29: Sidecar lifecycle
**Trigger:** REST `POST /api/sidecars/{name}/{start|stop|health}` lub
per-tab refcount

**Behavior:**
- R29.1 — `start(name, manifest)` idempotent — refcount++
- R29.2 — `subprocess.Popen(argv, start_new_session=True, env={**os.environ, **manifest.env})`
- R29.3 — `stop(name)` SIGTERM → SIGKILL po 5s, idempotent
- R29.4 — `is_running(name)` — proc.poll() is None
- R29.5 — `HealthChecker.ping(url, timeout=2.0)` — 4xx counts as up,
  5xx/timeout/refused as down

---

### R30: Per-tab refcount
**Trigger:** open/close Claude tab

**Behavior:**
- R30.1 — Tab.enabled_plugins zawiera nazwy sidecarów do auto-start
- R30.2 — On open: `_sidecar_acquire(name)` — refcount++ (start jeśli
  refcount: 0→1)
- R30.3 — On close: `_sidecar_release(name)` — refcount-- (stop jeśli
  refcount: 1→0)
- R30.4 — REST `PUT /api/tabs/{idx}/plugins` — bulk update enabled_plugins
  + diff acquired/released sidecars

### R30a: Per-tab plugin missing on tab open *(decyzja: 2026-05-04)*

**Wymaganie:** tab przechowuje nazwy pluginów które miał, nawet jeśli
plugin jest niedostępny (deinstaled, plik usunięty, błąd ładowania).
Przy próbie otwarcia tabu z brakującym pluginem — dialog pyta usera.

**Behavior:**
- R30a.1 — `tab.enabled_plugins` jest persistowany w `claude_config`
  jako pełna lista nazw, niezależnie od dostępności pluginów
- R30a.2 — Przy `open_claude_tab(config)`: dla każdego `name` w
  `enabled_plugins`:
  - jeśli plugin obecny w `app._plugins` lub `app.sidecar_manifests` →
    acquire normalnie
  - jeśli BRAKUJE → modal dialog:
    "Plugin '{name}' (przypisany do tej sesji) nie jest dostępny.
    Czy chcesz go odłączyć od sesji?"
    - [Odłącz] → `enabled_plugins.remove(name)`, save config
    - [Zachowaj] → name pozostaje na liście; gdy plugin się pojawi
      (np. install) — następne otwarcie tabu acquire'uje go
- R30a.3 — Decyzja per-plugin (jeśli brakuje wielu, dialog x N) — albo
  bulk dialog z checkboxami (do wyboru implementacja)
- R30a.4 — Dialog tylko gdy tab otwierany przez user click; REST
  `POST /api/tabs/claude` z brakującym pluginem zwraca 400 z listą
  brakujących

---

## 12. Stats bar [Claude-only → uogólnić]

### R31: Stats display
**Trigger:** Claude tab opened

**Behavior:**
- R31.1 — Bar packed pod terminalem (height 44px)
- R31.2 — 11 fields: duration, prompts, responses, tokens in/out,
  cache hit %, cost, throughput tok/h, model, plan usage 5h, plan
  usage 7d
- R31.3 — Refresh co 5s (`GLib.timeout_add(5000, _update)`)

---

### R32: Session log reader [Claude-only]
**Behavior:**
- R32.1 — `_SessionStatsReader` szuka JSONL w
  `~/.claude/projects/<sanitized_project>/`
- R32.2 — Sanitization: `re.sub(r'[^a-zA-Z0-9-]', '-', project_dir)`
- R32.3 — Picks newest JSONL with mtime ≥ session start (caches once)
- R32.4 — Iteruje linie, sumuje tokeny per assistant message,
  ekstrahuje model z `message.model`

**Q32.1:** Co gdy session JSONL dopiero się tworzy (Claude jeszcze
nic nie zapisał)? Obecnie: zwraca `result` z zerami, nie cache'uje.

---

### R33: Cost calculation [Claude-only]
**Behavior:**
- R33.1 — `_STATS_PRICING` dict per-model (Opus/Sonnet/Haiku)
- R33.2 — Default `{input: 3, output: 15, cache_read: 0.30, cache_write: 3.75}`
  USD per million tokens
- R33.3 — Cost = sum components / 1M

---

### R34: Plan usage API [Claude-only]
**Behavior:**
- R34.1 — Background thread fetcher co 60s (`_USAGE_TTL`)
- R34.2 — GET `https://api.anthropic.com/api/oauth/usage` z OAuth
  token z `~/.claude/.credentials.json`
- R34.3 — Display: `5h <pct>%` + tooltip z `resets_at`
- R34.4 — Stale: `5h –` (no token / API down)
- R34.5 — Credentials są **per-user** (Linux user account) — brak
  shared'a między userami systemu. Każdy user systemu Linux ma własny
  `~/.claude/.credentials.json` i widzi własne usage. *(potwierdzone:
  2026-05-04)*

---

## 13. Skills panel

### R35: Skills discovery
**Sources:**
- R35.1 — Bundled: `bterminal/<package>/defaults/skills/*.md` (BTerminal-shipped)
- R35.2 — User: `~/.claude/commands/*.md`

**Behavior:**
- R35.3 — `SkillsPanel` listuje oba źródła
- R35.4 — Click → render markdown body w detail pane

**Q35.1:** Skills są **Claude-specific** (`~/.claude/commands/`) czy
provider-agnostic? Czy Copilot/Aider mają własne skills systems?

---

## 14. Files panel

### R36: Project file browser
**Trigger:** `FilesPanel` w sidebarze

**Behavior:**
- R36.1 — Project dropdown — wybór z saved Claude sessions
- R36.2 — Tree view ze plików (gitignore-aware?)
- R36.3 — Right-click context menu: Copy Path, Copy Relative, Copy Name,
  Open With, Diff with commit
- R36.4 — Symlinki: follow vs nie? (komentarz w kodzie sugeruje smart
  detection)

---

### R37: Diff with commit
**Trigger:** prawym → "Diff with commit"

**Behavior:**
- R37.1 — Dialog z lista commitów z `git log`
- R37.2 — Wybór commit'u → `meld <file> <(git show <commit>:<path>)`

---

## 15. Git panel

### R38: Right-side panel
**Trigger:** Claude tab visible (panel hidden dla SSH/local)

**Behavior:**
- R38.1 — Accordion: Status, Branches, Commits, Stash
- R38.2 — Live update via `Gio.FileMonitor` na .git/ + working tree
- R38.3 — Numstat per file (added/removed)
- R38.4 — Toggle visible via menu / REST

---

## 16. Consult panel

### R39: External AI consultations
**Trigger:** `ConsultPanel`, sidebar tab "Consult"

**Behavior:**
- R39.1 — Manage OpenRouter API key + 9 modeli (default config)
- R39.2 — Per-project tribunal presets (analyst/advocate/critic/arbiter)
- R39.3 — Send query → `consult` CLI tool
- R39.4 — `consult debate` mode — multi-model debate

---

## 17. Theme system

### R40: Catppuccin palettes
**Behavior:**
- R40.1 — Mocha (dark default) + Latte (light)
- R40.2 — Identyczne keys (26 colors) — gwarancja `_build_css`
- R40.3 — Terminal palette (16 ANSI) per-theme

---

### R41: Theme toggle
**Trigger:** "☀/☾" button w nagłówku

**Behavior:**
- R41.1 — Switch palette → `CATPPUCCIN.update(other)`,
  `TERMINAL_PALETTE[:] = ...`
- R41.2 — `_OPTIONS["theme"]` zapisany → persist
- R41.3 — CSS regenerated → `css_provider.load_from_data(CSS.encode())`
- R41.4 — Wszystkie open VTE terminals: `set_colors(fg, bg, palette)`
  → już wydrukowany text TEŻ przekoloruje się (VTE re-renderuje cały
  scrollback z nową paletą — slot 1 ANSI green pre/post toggle ma inny
  hex, ale ten sam slot). To zachowanie domyślne VTE i preferowane.
  *(potwierdzone: 2026-05-04)*

---

## 18. Auto-update + errata

### R42: Update check
**Trigger:** boot (R1.6) lub Tools menu → "Sprawdź aktualizacje"

**Behavior:**
- R42.1 — `git fetch origin` w `REPO_DIR`
- R42.2 — Compare local HEAD vs origin/master
- R42.3 — Jeśli behind: pokaż `_prompt_update` z log + errata
- R42.4 — User klikn "Update" → **PRE-UPDATE WARNING DIALOG** (R42a)
- R42.5 — Po akceptacji: `_do_update` runs `install.sh` w modal z live
  log + progress bar
- R42.6 — Success → `_restart_bterminal` (exec self) — wszystkie taby
  i sidecary tracone
- R42.7 — Failure → rollback dialog (z `BTERMINAL_ROLLBACK_OK` marker
  w stderr)

### R42a: Pre-update consent dialog *(decyzja: 2026-05-04)*

**Wymaganie:** przed startem `install.sh` BTerminal MUSI ostrzec usera
o resetowaniu wszystkich aktywnych sesji i poprosić o jawną zgodę.

**Behavior:**
- R42a.1 — Dialog tytuł: "Aktualizacja wymaga zresetowania sesji"
- R42a.2 — Treść: lista co się wydarzy:
  - "Po update'cie zostaną zamknięte:
     - {N} aktywnych tabów (SSH/Claude/local)
     - {M} działających sidecarów
     - Wszystkie niezapisane macra / configi pozostaną w plikach
     - Auto-trigger zadań zostanie zatrzymany
   Następnie BTerminal zrestartuje się automatycznie.
   Kontynuować?"
- R42a.3 — Buttons: [Zaktualizuj] [Anuluj]
- R42a.4 — Anuluj → return without running install.sh
- R42a.5 — Zaktualizuj → kontynuacja (R42.5+)

**Powód:** user musi wiedzieć że niezapisana praca w terminalach (np.
edytor otwarty w SSH) zostanie utracona. Implicit restart był
zaskakujący.

---

### R43: Errata viewer
**Trigger:** Tools → "Errata"

**Behavior:**
- R43.1 — `errata.json` w REPO_DIR — lista wpisów `{version, date, summary}`
- R43.2 — Sort: najnowsza pierwsza (index 0)

---

## 19. Debug REST API

### R44: REST server bootstrap
**Trigger:** `--debug-rest` flag / `BTERMINAL_DEBUG_REST=1`

**Behavior:**
- R44.1 — Bind `127.0.0.1:7780` (override via `BTERMINAL_DEBUG_REST_PORT`)
- R44.2 — Title bar suffix `[DEBUG-REST :7780]`
- R44.3 — Czerwony pasek 2px nad notebookiem (visual marker)
- R44.4 — Token rotation: nowy token co startup, file `~/.config/bterminal/debug_token`
  chmod 0600
- R44.5 — Wszystkie requests audited w `~/.cache/bterminal/debug-rest.log`
- R44.6 — Idle watchdog: shutdown po `BTERMINAL_DEBUG_IDLE_TIMEOUT=1800`s
  silence

---

### R45: REST routes
**Surface:**
- R45.1 — 10 GET routes: `/api/health`, `/state`, `/tabs`, `/plugins`,
  `/sidecars`, `/sidecars/{n}/health`, `/tabs/{i}/plugins`,
  `/tabs/{i}/intro_prompt`, `/window/screenshot`, `/debug/log`
- R45.2 — 1 PUT route: `/api/tabs/{i}/plugins`
- R45.3 — 15 POST routes: tabs/local, tabs/claude, tabs/{i}/{close,feed,key,
  simulate_prompt,force_idle}, window/{toggle_sidebar,sidebar/show,toggle_git_panel},
  quit, plugins/{n}/{enable,disable}, sidecars/{n}/{start,stop}
- R45.4 — Bearer auth required (Authorization header), `secrets.compare_digest`

---

## 20. Configuration persistence

### R46: Options
**File:** `~/.config/bterminal/options.json`

**Schema:**
- R46.1 — `theme: "dark"|"light"`, `font: "Monospace 11"`, `shell: ""`,
  `check_updates_on_start: true`
- R46.2 — Atomic write via tempfile + os.replace
- R46.3 — Defaults merged on load (forward-compat)

---

### R47: Repo path
**File:** `~/.config/bterminal/repo_path`

**Behavior:**
- R47.1 — Zapisany przez install.sh przy każdej instalacji
- R47.2 — Czytany przez `config.REPO_DIR` na boot
- R47.3 — Updater + errata viewer używają

---

## 21. Installer

### R48: install.sh contract
**Trigger:** `./install.sh [--no-sudo]`

**Behavior:**
- R48.1 — Sprawdza Python ≥3.10, Node ≥18, npm ≥9 (vs `dependencies.json`)
- R48.2 — Instaluje Claude Code via npm prefix `~/.npm-global`
- R48.3 — Instaluje system tools (meld, pandoc, latex — auto z apt)
- R48.4 — Kopiuje `bterminal/` package + 5 CLI tools z `tools/`
- R48.5 — Tworzy launcher `~/.local/share/bterminal/bterminal-launcher`
  + symlink `~/.local/bin/bterminal`
- R48.6 — Rollback on failure: backup → cp -f restore → `BTERMINAL_ROLLBACK_OK`

**Q48.1:** Test fresh install na czystym systemie (Docker) nie był
nigdy automatyzowany. Najsilniejszy regression risk.

---

### R49: Errata feed
**Behavior:**
- R49.1 — `errata.json` live symlinkowany z repo do INSTALL_DIR
- R49.2 — Update'owany przez git pull, nie wymaga reinstall

---

## 21a. Keyboard shortcuts *(2026-05-04)*

### R49a: Skróty klawiszowe — discoverability
**Wymaganie:** istniejące skróty zostają bez zmian. Dodać miejsce
gdzie user może je odczytać.

**Behavior:**
- R49a.1 — Tools menu → "Skróty klawiszowe" (lub Help → Shortcuts)
- R49a.2 — Modal dialog z listą wszystkich shortcut'ów BTerminala +
  zarejestrowanych przez aktywne pluginy (`plugin.get_keyboard_shortcuts()`)
- R49a.3 — Format tabeli:
  | Skrót | Akcja | Źródło |
  | Ctrl+T | Nowy tab lokalny | core |
  | Ctrl+W | Zamknij tab | core |
  | F11 | Fullscreen | core |
  | Ctrl+, | Options | core |
  | Ctrl+Shift+V | Paste (z detekcją obrazów) | core |
  | ... | ... | ... |
- R49a.4 — Shortcut'y są **read-only** w tym dialogu — nie da się ich
  zmienić w UI
- R49a.5 — Pluginy autoreport'ują własne shortcut'y — appendowane do tabeli

**Q49a.1:** lista core shortcut'ów wymaga dokładnego inwentarza z kodu
— wykonam to przy implementacji R49a (audit `key-press-event` connect'ów +
`Gtk.AccelGroup` w BTerminalApp).

## 21b. Window state *(2026-05-04)*

### R49b: Window position/size — NIE zapamiętywane
**Decyzja:** BTerminal **nie pamięta** pozycji ani rozmiaru okna między
uruchomieniami. Każdy boot = default 1200x700 na środku ekranu.

**Powód:** uproszczenie state, brak conflictów na multi-monitor setupach,
przewidywalne zachowanie. *(potwierdzone: 2026-05-04)*

## 22. CLI tools

### R50: ctx
**Surface:** `ctx --help`
- R50.1 — `ctx init <name> [--shared]`
- R50.2 — `ctx set <project> <key> <value>`
- R50.3 — `ctx append <project> <key> <value>`
- R50.4 — `ctx get <project> [--shared]`
- R50.5 — `ctx summary <project> "<text>"`

---

### R51: tasks
**Surface:** `tasks --help`
- R51.1 — `tasks add <project> [<id>] <desc>`
- R51.2 — `tasks done <project> <id>`
- R51.3 — `tasks list <project>`
- R51.4 — `tasks context <project> [--session <id>]`
- R51.5 — `tasks pending <project>`

---

### R52: consult
**Surface:** `consult --help`
- R52.1 — `consult "<query>"`
- R52.2 — `consult -m <model> "<query>"`
- R52.3 — `consult -f <file> "<query>"`
- R52.4 — `consult debate "<problem>"` (tribunal)
- R52.5 — `consult models` (list)

---

### R53: memory_wizard
**Surface:**
- R53.1 — `memory_wizard <project> --project-dir <dir> [--dry-run]`
- R53.2 — Logs analysis → rule proposals → save (after confirm)

---

### R54: claude_log
**Surface:**
- R54.1 — `claude_log collect <project_dir> [<jsonl_path>]`
- R54.2 — Copy active session JSONL → `<project>/claude_log/`

---

### R55: Per-tab sidebar binding

Sidebar panele (Plugins, Files, Skills, Memory, Tasks, Ctx) są **per-tab
aware**: przełączenie zakładki w notebook → panele reloadują stan
względem aktywnego taba (jego `claude_config.project_dir`,
`enabled_plugins`, `_task_project`). Bez tego user widzi globalny widok
identyczny dla wszystkich tabów co myli — np. plugin pokazany jako
Loaded chociaż w aktywnej sesji nie jest do niej wstrzykiwany.

**Surface:**
- R55.1 — Każdy panel exposes `set_active_tab(tab) -> None` o uniform
  signature. Wywoływane przez `App._on_switch_page`.
- R55.2 — Plugins panel: Scope label "globalny" lub
  "<tab_name> (per-projekt)". Status "Off (tab)" gdy plugin jest
  globally Loaded ale per-tab uncheck.
- R55.3 — Per-tab toggle pluginu modyfikuje `tab.enabled_plugins`
  in-memory + persist do `claude_sessions.json` via
  `claude_manager.update`. Globalny config niezmieniony.
- R55.4 — Tab bez `claude_config` (SSH/local) → panele zachowują
  poprzedni stan lub wyświetlają global view.

**Edge cases:**
- E55.1 — Tab z `enabled_plugins=None` (klucz nieobecny w session
  config — backwards compat) → checkbox per-tab odzwierciedla globally
  enabled plugins. Pierwszy klik tworzy explicit set.
- E55.2 — Tab z `enabled_plugins=[]` (explicit empty) → wszystkie
  checkboxy unchecked.

---

# Open questions — discussion list

## ✅ Zamknięte (2026-05-04)

**Q1.1** — ~~Single-instance vs multi-instance?~~ **multi (NON_UNIQUE) zostaje**
**Q2.2** — ~~Sidecary przeżywają crash BTerminal — celowe?~~ **OK, zostaje**
**Q3.1** — ~~Plain-text passwords?~~ **NIE — in-memory only** (R3a, do refactoringu)
**Q4.1** — ~~Migracja claude_sessions → ai_sessions?~~ **TAK** — uniwersalny manager
            + provider capability flags (R4, R4a, R4b)

**Decyzje boot/CLI (R1):**
- R1.1 — strict argparse (error na nieznane flagi)
- R1.2 — tylko flag `--debug-rest`, env var dropped
- R1.f2 — corrupt options.json → dialog + self-heal (overwrite defaults)

**Decyzje SSH (R5):**
- Q5.1 — ~~Macros: regex wait?~~ **NIE — tylko delay**

**Decyzje tabs (R7, R8, R9):**
- Q7.1 — ~~tab reorder dla Claude?~~ **TAK** (provider-agnostic)
       + nowy R7a — **wizualny marker providera** na zakładce
       (icon + color, z `providers.json:display`)
- Q8.1 — ~~`project_dir` nie istnieje?~~ **dialog "Utworzyć?" [Utwórz]/[Anuluj]**
- Q9.1 — ~~GUI close na auto-trigger tabie?~~ **dialog potwierdzenia**
       (symetria z REST 409 conflict)

**Decyzje od audytu (10 punktów, 2026-05-04):**
1. Błędy AI CLI → output w terminalu (R8a)
2. `auto_start: true` zastąpione przez `task_bound: true` (cleaner naming, R28a)
3. Globalny disable pluginu nie wpływa na aktywne taby — "next session"
   semantyka (R27a)
4. Theme toggle re-coloruje też istniejący scrollback (default VTE) (R41.4)
5. Brakujący plugin per-tab → dialog "Odłączyć?" [Odłącz]/[Zachowaj] (R30a)
6. Pre-update consent dialog z listą co zostanie zresetowane (R42a)
7. Log retention 20 rozmów per-projekt (R23a)
8. Pozycja/rozmiar okna **NIE** zapamiętywane (R49b)
9. Tools menu → "Skróty klawiszowe" — read-only lista (R49a)
10. Claude credentials per-user systemowy, brak sharing'u (R34.5)

**Pozostałe decyzje (17 Q's, 2026-05-04 — analiza propozycji + akceptacja):**
- Q2.1 — Quit confirm tylko gdy ≥1 aktywna sesja, format z liczbą sesji
- Q7a.1 — Emoji default + opcjonalny SVG override (`display.icon_path`)
- Q7a.2 — `🤖` fallback + warning w stderr dla niezdefiniowanego providera
- Q11.1 — Order: header → rules → ctx → plugins → sidecars → tools_help → custom_prompt
- Q11.2 — Universal markdown base + per-provider override hook
- Q13.1 — Tools help: content uniwersalny, format dziedziczy z Q11.2
- Q15.1 — Drop pending inject na tab close (akceptowane zachowanie)
- Q15.2 — Idle 30s + prompt regex detection (first-to-fire)
- Q17.1 — Wizard ma "Skip" + banner w tabie z instrukcją `ctx init`
- Q19.1 — Round-trip integrity test obowiązkowy (export → import → re-export → eq)
- Q21.2 — Concurrent OK (atomic claim wystarcza, parallel work feature)
- **Q21.3 — Claude-only NA RAZIE + #TODO w `terminal_tab.py:_on_task_idle_timeout`**
  na refactor multi-CLI w przyszłości
- Q23a.1 — Per-project 20 (override przez `_OPTIONS["claude_log_retention"]`)
- Q28a.1 — Drop `auto_start`, dodać `task_bound: true` (cleaner naming)
- Q32.1 — Zero state na missing JSONL (current, akceptowane)
- **Q35.1 — Claude-only NA RAZIE + #TODO w `panels/skills.py`** na per-provider
  user_skills_dir
- Q48.1 — Docker installer test obowiązkowy w CI

## 🔵 ~~Propozycje moje~~ — wszystkie zaakceptowane / skorygowane (2026-05-04)

(zachowane jako rationale dla decyzji powyżej)


**Q2.1 → TAK, ale smart.** Dialog tylko gdy są aktywne sesje. Format:
"BTerminal ma {N} aktywnych sesji ({M} SSH, {K} Claude, {L} sidecarów).
Zamknąć?" [Tak] [Anuluj]. Symetria z R42a + R9.f2.

**Q7a.1 → Emoji default + SVG override.** Emoji jako string w label
(zero asset pipeline), `display.icon_path` w providers.json jako
opcjonalny override do GTK.Image. BTerminal już jest emoji-heavy
(stats bar, theme toggle), spójność.

**Q7a.2 → `🤖` fallback + warning w stderr.** Niezdefiniowany providers
display = config bug, ale degraduje gracefully.

**Q11.1 → ctx pierwsze, custom prompt ostatni** (current behavior +
explicite zapisane). Ordering: header → rules → ctx → plugins →
sidecars → tools_help → custom_prompt. System context przed user intent =
prompt engineering best practice; custom najbliżej miejsca gdzie user
będzie pisał.

**Q11.2 → Universal markdown base + per-provider override.**
`AICLIProvider.build_intro_prompt(sections)` ma default impl (markdown
`##` joined by `\n\n`). Provider override jeśli format inny (np. Aider
plain text). Capability `intro_prompt: false` całkowicie wyłącza.

**Q13.1 → Content uniwersalny, format dziedziczy z Q11.2.**
Tools help opisuje BTerminal CLI (ctx, consult, tasks, memory_wizard,
claude_log) — to jest jednakowe niezależnie od AI providera. Format
markdown vs plain dziedziczy z `provider.build_intro_prompt`.

**Q15.1 → DROP pending inject na close (current).** Nie warta
komplikacji persistence. User zamykający tab moves on; reopening
wzowi się intro prompt z rules od zera. Akceptowane zachowanie, nie bug.

**Q15.2 → Idle timeout 30s + prompt regex detection.**
Aktualne 10s za agresywne (Claude może myśleć dłużej). Solution:
1. Zwiększyć `_TASK_IDLE_TIMEOUT_SEC` z 10 → 30
2. Dodać heurystykę: w `_on_contents_changed_tasks` skanować ostatnie
   linie przez regex prompt'u (`r'^>$|^\$ $|^❯ '`). Match → fire wcześniej.
First-to-fire wygrywa (timer LUB prompt match).

**Q17.1 → Skip allowed z warningiem.** Wizard ma "Skip" button na
pierwszym kroku. Po skip:
- Claude config zapisany (tab otwiera się normalnie)
- ctx project NIE utworzony
- W tabie banner: "Ctx not initialized — run `ctx init <name>` for
  full features"
- User może retry'ować przez `ctx init` z CLI lub re-open dialog'u

**Q19.1 → TAK — round-trip integrity test obowiązkowy.** Test plan:
```python
def test_ctx_export_import_roundtrip(tmp_path):
    # setup project z entries + image attachment
    # export → JSON file (file1)
    # import file1 do fresh DB → re-export → file2
    # assert json.loads(file1) == json.loads(file2)
```

**Q21.2 → Concurrent OK (current atomic claim).** SQLite
UNIQUE(project, task_id) na task_claims wystarcza. Dwa taby z tym
samym project, atomic claim podzielisz między nie. Document as feature
(parallel work na większym projekcie).

**Q21.3 → Claude-only TERAZ, #TODO na przyszłość** *(decyzja: 2026-05-04)*

Auto-trigger pozostaje hardcoded pod Claude (jak obecnie — wymaga
`tab.claude_config` + `tab._stats_bar`). Generalizacja na inne CLI to
przyszły refactor.

**#TODO w kodzie (do dodania w Etapie testów/refactoru):**
W `bterminal/ui/terminal_tab.py:_on_task_idle_timeout` dopisać komentarz:
```python
# TODO(provider-abstraction): generalizacja na multi-CLI.
# Wymaga:
#   1. Provider capability flag `task_auto_trigger: bool` w providers.json
#   2. Przeniesienie logiki z TerminalTab.{_on_task_idle_timeout,_claim_next_task}
#      do generic AISessionMixin lub Provider.handle_idle()
#   3. Decoupling od _stats_bar (obecnie wymagane dla _stats_bar.claude_config check)
#      → użyj tab.provider zamiast claude_config
#   4. Test contract: mock provider z capability=true + scenario z 3 taskami,
#      assert że trigger fires na każdym idle
```

**Q23a.1 → Per-project 20** (już proponowane, confirmed). Storage
minimal, brak cross-contamination, override przez `_OPTIONS["claude_log_retention"]`.

**Q28a.1 → Drop `auto_start`, dodać `task_bound: true`.** Cleaner
naming reflecting actual semantic. Behavior:
- `task_bound: true` → sidecar startuje gdy DOWOLNY projekt ma
  autorun=1 (refcount-like na poziomie globalnym)
- `task_bound: false` (default) → sidecar tylko on-demand przez
  per-tab refcount
- Migracja: `auto_start: true` → `task_bound: true` w manifest schema
- Żaden z 5 example'ów w `examples/sidecars/` nie używa flagi → brak
  realnej migracji

**Q32.1 → Zero state (current).** Reader returns zeros, doesn't cache,
nie crashuje. Document as expected behavior. Stats bar wyświetla
"-" gdzie value == 0 i nie ma session timeline.

**Q35.1 → Claude-only TERAZ, #TODO na przyszłość** *(decyzja: 2026-05-04)*

Skills logic pozostaje obecna:
- Bundled: `defaults/skills/` (uniwersalne markdown — działają z każdym
  CLI który markdown rozumie)
- User: tylko `~/.claude/commands/` (Claude path hardcoded w SkillsPanel)

Per-provider user skills directories to przyszły refactor.

**#TODO w kodzie:**
W `bterminal/ui/panels/skills.py` (SkillsPanel) dopisać komentarz:
```python
# TODO(provider-abstraction): per-provider user-skills dirs.
# Wymaga:
#   1. Capability `user_skills_dir: str | None` w providers.json:
#      claude → "~/.claude/commands"
#      aider  → "~/.aider/commands"   (TBD when aider supports)
#      copilot → null                  (no skill concept)
#   2. SkillsPanel czyta merged view: bundled + active providers' dirs
#   3. Refresh button → re-scan wszystkich dirów
#   4. UI: per-skill badge "{provider}" wskazujący źródło
#   5. Test: mock provider z user_skills_dir → assert skills are loaded
```

**Q48.1 → TAK — Dockerfile + GitHub Actions.** Manual install.sh test
na czystej VM jest zbyt wolny dla CI. Implementation:
- `tests/installer/Dockerfile` — ubuntu:24.04 fresh
- `tests/installer/test_install.sh` — runs install.sh, smoke checks
- `.github/workflows/ci.yml` — step "installer-docker" obowiązkowy
  przed merge do master
- Czas: ~3-5 min build + run, akceptowalne

## 🔓 ~~Nadal otwarte~~ — pusta lista, spec kompletna ✅

**Wszystkie 17 + 10 audytowych pytań zamknięte.**

Doc gotowy do mapowania `R<N>` → testy + implementację.
**Q5.1** — Macros: tylko delay czy też "wait for prompt regex"?
**Q7.1** — Tab reorder dla Claude tabs jest pożądane?
**Q8.1** — Behavior gdy `project_dir` nie istnieje?
**Q9.1** — GUI close button na auto-trigger tabie — confirm dialog?
**Q11.1** — Custom prompt vs ctx output — kolejność?
**Q11.2** — Provider-agnostic intro prompt format vs per-provider?
**Q13.1** — Tools help block per-provider?
**Q15.1** — Drop pending inject przy zamknięciu tabu — OK?
**Q15.2** — Inject może odpalić w środku odpowiedzi Claude — co robimy?
**Q17.1** — Ctx wizard powinien być Skip-pable?
**Q19.1** — Export/Import round-trip = full integrity?
**Q21.1** — `_reset_all_autorun` na startup celowy?
**Q21.2** — Race condition gdy 2 tabs ten sam project?
**Q21.3** — Auto-trigger provider-agnostic czy Claude-only?
**Q32.1** — JSONL nie istnieje — graceful zero state?
**Q35.1** — Skills Claude-only czy każdy provider ma własne?
**Q48.1** — Docker installer test — wymaga automatyzacji?

---

**Następny krok:** dyskusja Q1-Q21+ z autorem, korekty wymagań,
potem mapowanie R<N> → test_name dla każdego.
