# Analiza: integracja drugiego CLI (GitHub Copilot) jako alternatywnego providera AI w BTerminal

**Data:** 2026-05-06
**Autor:** Claude (Sonnet 4.6) — analiza zlecona przez DexterFromLab
**Status:** Dokument analityczny — brak działań programistycznych. Decyzje kierunkowe do akceptacji przed implementacją.
**Powiązane:** `docs/REQUIREMENTS.md` sekcje R4 / R4a / R4b / R7a (już zaplanowane), `docs/refactor-modular-architecture.md`.

---

## 0. TL;DR (decyzja w 6 punktach)

1. **GitHub Copilot CLI 2026** (nie `gh copilot` extension) jest pełnoprawnym agentic CLI — odpowiednikiem Claude Code. Komenda: `copilot`. Pakiet: `@github/copilot`. GA: 2026-02-25. Jest realnym kandydatem na drugiego providera.
2. **REQUIREMENTS.md już zawiera architekturę** (R4/R4a/R4b/R7a) — `providers.json` + capabilities flags + `ai_sessions.json`. Tę architekturę należy zaimplementować, nie projektować od nowa.
3. **Tools użytkownika (ctx, tasks, consult, memory, skills, files, plugins) są w 95 % provider-agnostic** — działają na danych BTerminala (CTX DB, tasks DB, ConsultManager, plugin runtime), a nie na rozmowie z modelem. Wymagają tylko, by intro prompt provider-X opisał te narzędzia.
4. **Główna praca to abstrakcja warstwy spawn / log parsing / stats / auto-trigger** — 31 zidentyfikowanych miejsc Claude-specific w kodzie. Tier 1 (config + dispatch) to ~15 dni, całość ~60–80 dni senior dev time.
5. **Wybór providera w sesji** = nowe pole `provider` w `ai_sessions.json` + osobny dialog (`AISessionDialog(provider)`). Domyślnie 2 providerzy do wyboru w GUI: Claude Code, GitHub Copilot.
6. **Krytyczne luki Copilota vs Claude:** brak deterministycznego "ready marker" (idle detection trzeba zrobić przez tail-f `events.jsonl`), brak publicznego "plan usage" API (`/usage` slash-command tylko w-sesji), inny format logów (SQLite + JSONL events, nie pojedynczy JSONL transcript).

---

## 1. Cel integracji (user requirements)

Z prompta użytkownika (2026-05-06):

> "Chciałbym aby user mógł wybrać Claude Code lub GitHub Copilot i aby narzędzia takie jak CTX, Tasks, Consult, Memory, Skills, Files oraz Plugins działały dokładnie tak samo. Najlepiej by było aby w sesji projektu można było wybrać w konfiguracji z jakim CLI uruchomić sesję."

Wymagania pochodne:

- **R-INT-1 — Wybór providera per sesja:** dialog konfiguracji sesji ma dropdown "AI provider: [Claude Code | GitHub Copilot]". Wybór jest trwały (zapis w `ai_sessions.json`).
- **R-INT-2 — Tools cross-provider:** wszystkie 8 paneli sidebar (consult, ctx_manager, files, git, memory, plugin_manager, skills, tasks) działają identycznie niezależnie od wybranego providera.
- **R-INT-3 — Identyczne intro prompt UX:** intro prompt opisuje te same narzędzia (ctx/tasks/consult/memory_wizard/skills) niezależnie od providera. Provider widzi te same instrukcje.
- **R-INT-4 — Identyczne workflow:** auto-trigger zadań, periodic rules injection, stats tracking — gdzie technicznie możliwe — działają w obu providerach.
- **R-INT-5 — Backward compat:** istniejące sesje (`claude_sessions.json`) muszą zostać zmigrowane bez utraty danych (R4b idempotent migration).
- **R-INT-6 — Wizualne odróżnienie:** zakładka pokazuje który provider działa (R7a — emoji + kolor).

---

## 2. Porównanie Claude Code vs GitHub Copilot CLI

### 2.1 Tabela szczegółowa (na podstawie oficjalnych docs 2026)

| Aspekt | Claude Code | GitHub Copilot CLI |
|---|---|---|
| Komenda | `claude` | `copilot` |
| Pakiet | `@anthropic-ai/claude-code` (npm) | `@github/copilot` (npm), `copilot-cli` (brew), winget, curl-bash |
| Wymagania runtime | Node 18+ | Node 22+ (npm), brak (binary), PowerShell 6+ (Windows) |
| Subskrypcja | Anthropic API key / Claude Pro / Max | GitHub Copilot Pro / Pro+ / Business / Enterprise |
| Auth headless | `ANTHROPIC_API_KEY` env | `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` env |
| Auth interaktywny | `claude login` (OAuth device-code) | `/login` w sesji (OAuth device-code) |
| Komenda headless | `claude -p "prompt"` | `copilot -p "prompt"` |
| Komenda interaktywna z auto-pierwszą wiadomością | (positional arg) | `copilot -i "prompt"` |
| Wznowienie ostatniej | `claude --continue` | `copilot --continue` (ten sam flag) |
| Wznowienie po ID | `claude --resume <uuid>` | `copilot --resume <uuid>` (ten sam flag) |
| "Yolo" / skip permissions | `--dangerously-skip-permissions` | `--yolo` lub `--allow-all` |
| Granularne permissions | brak (full or none) | `--allow-tool 'shell(rm)'`, `--deny-tool 'shell(curl)'`, `--allow-all-paths`, `--allow-all-urls`, `--add-dir PATH` |
| Output JSON | `--output-format stream-json` (newline-delimited events) | `--output-format json` (newline-delimited events) |
| Tryby agenta | (interactive default) | `--mode interactive\|plan\|autopilot`, shorthand `--plan`, `--autopilot` |
| Plan mode | brak (jest Extended Thinking) | tak — Shift+Tab toggle lub `--plan` |
| Autopilot N-step | brak (każdy continue ręczny) | `--autopilot --max-autopilot-continues N` |
| Wybór modelu | `--model <name>`, `/model` w sesji | `--model auto\|<name>`, default Sonnet 4.5 (2026) |
| Reasoning effort | (Extended Thinking via prompt) | `--effort low\|medium\|high` (alias `--reasoning-effort`) |
| Custom agents | `.claude/agents/<name>.md` | `--agent NAME` + `/agent` slash command |
| Subagents | wbudowany Task tool | `/fleet PROMPT` (parallel), `/delegate` (cloud agent → PR) |
| Context file repo | `CLAUDE.md` (cumulative parent dirs) | `AGENTS.md` (root only), `.github/copilot-instructions.md` (legacy), `.github/instructions/**/*.instructions.md` |
| Context global user | `~/.claude/CLAUDE.md` | `~/.copilot/instructions.md` (jeśli istnieje) |
| Auto-compact | `/compact`, threshold konfigurowalny | `/compact`, threshold 95 % |
| Skills | `.claude/skills/<name>/SKILL.md` (frontmatter + body) | `/skills [list\|info\|add\|remove\|reload]` (lokalne extension packs) |
| Plugins | `.claude/plugins/<name>/` | `/plugin [marketplace\|install\|uninstall]` + flag `--plugin-dir` (jest marketplace) |
| MCP support | tak — `~/.claude.json mcpServers` | tak — `~/.copilot/mcp-config.json`, wbudowany GitHub MCP, `/mcp [add\|edit\|delete\|disable\|auth]` |
| Hooks lifecycle | tak — 14 events (PreToolUse, PostToolUse, UserPromptSubmit, Stop, SessionStart, ...) | brak hierarchii hooks — `.allowed-tools` + custom prompts |
| Statusline | `statusLine` w settings.json (custom command) | `/statusline` slash, `/footer` slash |
| Slash commands built-in | `/compact /clear /resume /model /context /status /help /export /hooks` | `/login /logout /usage /context /compact /init /instructions /skills /plugin /mcp /agent /fleet /delegate /allow-all /deny-tool /yolo /chronicle /statusline /footer` |
| Format historii sesji | jeden plik JSONL na sesję: `~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl` | dwa źródła: `~/.copilot/session-state/<uuid>/events.jsonl` + `~/.copilot/session-store.db` (SQLite z indeksem FTS5) |
| Sanitizacja path | `/`→`-`, `~`→`-` | nieformalnie udokumentowana |
| Token tracking | `usage{input,output,cache_creation,cache_read}` w każdym evencie + `total_cost_usd` | `events.jsonl` → `session.shutdown.modelMetrics.*.requests.cost`; `/usage` slash w sesji |
| Plan-usage API publiczne | `https://api.anthropic.com/api/oauth/usage` (nieoficjalne, używane przez SessionStatsBar) | brak publicznego API — `/usage` tylko interaktywne |
| "Ready marker" / idle detection | system event `type:"system", subtype:"stop"` w stream-json | brak deterministycznego markera — trzeba parsować `events.jsonl` (`tool.execution_complete` → brak nowego `tool.execution_start` przez N s, lub `session.shutdown` przy `-p`) |
| Extra (Copilot-only) | — | `--connect <id>` (remote session), `/chronicle` (FTS search w historii), wbudowany GitHub MCP server (issue/PR/code), `--no-mouse`, `--plain-diff`, `--no-banner` |

### 2.2 Kluczowe różnice z perspektywy BTerminal

**A. Idle detection — najtrudniejsza różnica.**
- Claude: stream-json wypluwa event `system.stop` → BTerminal może nasłuchiwać deterministycznie.
- Copilot: brak takiego markera. Trzeba albo (a) wymusić `--output-format json` i parsować eventy ze stdout, albo (b) tail-ować `~/.copilot/session-state/<uuid>/events.jsonl` osobnym wątkiem. Każde rozwiązanie zwiększa złożoność implementacji `_on_task_idle_timeout`.
- **Rekomendacja:** opcja (a) — `--output-format json` + parser eventów stdout. Wymaga uruchamiania Copilota w trybie nie-TUI (lub TUI z dual output do pty + log dir). Wymaga eksperymentu w Etapie 2.

**B. Plan-usage API.**
- Claude: BTerminal woła `https://api.anthropic.com/api/oauth/usage` co 60 s i pokazuje 5h/7d windows.
- Copilot: brak publicznego endpointu. `/usage` jest interaktywne, ale BTerminal nie może go wywołać programowo bez wstrzykiwania komend do TUI (kruche).
- **Rekomendacja:** SessionStatsBar dla Copilota pokazuje tylko aktualne tokeny/cost z `events.jsonl`. Plan-usage % = ukryte (capability flag `usage_api: false`).

**C. Format logu sesji.**
- Claude: jeden JSONL plik per sesja, znana ścieżka, łatwa analiza.
- Copilot: SQLite indeks (`session-store.db`) + `events.jsonl` per sesja. SQLite jest zaletą (FTS5 search, można robić rich session picker), ale wymaga osobnego parsera.
- **Rekomendacja:** dla Tier 2 zrobić tylko `events.jsonl` parser; SQLite session picker (Q ulepszenie) odłożyć na później.

**D. Permission model.**
- Claude: binarny — albo full permissions albo nic.
- Copilot: granularny — `--allow-tool 'shell(rm)'` etc.
- **Implikacja:** dialog konfiguracji sesji Copilota powinien pokazywać dodatkowe pole "Allowed tools list" (advanced). Domyślnie `--yolo` jak Claude `--dangerously-skip-permissions`.

**E. Context file.**
- Claude czyta `CLAUDE.md` cumulatively (parent directories).
- Copilot czyta tylko root-level `AGENTS.md`.
- BTerminal generuje `CLAUDE.md` w `_init_ctx_in_project_dir()`. Trzeba: albo (a) generować oba pliki dla obu providerów (duplikacja), albo (b) generować tylko ten potrzebny dla wybranego providera, albo (c) generować jeden symboliczny i drugi link.
- **Rekomendacja:** opcja (a) — generować oba (lub: `ln -s CLAUDE.md AGENTS.md`). Treść jest identyczna.

**F. Plugins / Skills cross-format.**
- Skills BTerminal-specific (8 paneli) są **niezależne od mechaniki skills/plugins providera** — to widget GTK, nie agent skill. Działają identycznie.
- Plugins BTerminala (in-process kontrakt) wstrzykują tekst do intro prompt — to jest agent-readable; działa identycznie dla Claude i Copilot.
- Skills providera (`.claude/skills/`, Copilot `/skills`) — to są **mechanizmy providera, nie BTerminala**. Provider sam je obsłuży w sesji. BTerminal nie dotyka.

---

## 3. Audyt obecnego kodu BTerminal (skondensowany)

Pełny audyt: 31 miejsc Claude-specific. Pogrupowanych w 13 kategorii. Każde z ocena trudności abstrakcji (LOW/MED/HIGH).

### 3.1 SPAWN / ARGV (HIGH)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/ui/terminal_tab.py:189-284` | `spawn_claude(config)` — bash script `claude_path {flags}{prompt_arg} && exec bash` | HIGH |
| `bterminal/ui/terminal_tab.py:229-233` | Hardcoded `--resume`, `--dangerously-skip-permissions` | MED |
| `bterminal/ui/terminal_tab.py:196-197` | `_find_claude_path()` — 7 candidate paths | MED |
| `bterminal/ui/terminal_tab.py:242-244` | Intro prompt jako bash-escaped CLI arg | HIGH |
| `bterminal/helpers.py:158-186` | `_find_claude_path()` (duplikat?) | LOW |
| `bterminal/ui/terminal_tab.py:248-270` | Sudo askpass helper — Claude-specific script | MED |

**Abstrakcja:** `spawn_ai_cli(provider_name, config)` — buduje argv z `provider.binary_search_paths`, `provider.argv_prefix`, `provider.flag_aliases` (mapowanie BTerminal → CLI flag).

### 3.2 SESSION MANAGEMENT (MED)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/config.py:36` | `CLAUDE_SESSIONS_FILE` const | LOW |
| `bterminal/models.py:180-184` | `ClaudeSessionManager(JsonListManager)` | LOW |
| `bterminal/models.py:180-184` | Schema bez `provider:` field | MED |
| `bterminal/ui/dialogs/claude_code.py:81-220` | `ClaudeCodeDialog` — Claude-only fields | HIGH |
| `bterminal/app.py:151,656-694` | `claude_manager`, `open_claude_tab(config)` | MED |

**Abstrakcja:** `AISessionManager` (single manager z `provider` field), rename pliku → `ai_sessions.json`, `open_ai_tab(provider, config)`.

### 3.3 LOG PARSING / STATS (HIGH)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/ui/stats.py:1-31` | SessionStatsBar reads JSONL + Anthropic API | HIGH |
| `bterminal/ui/stats.py:27-29,132` | Hardcoded `_CLAUDE_PROJECTS_DIR`, `_CLAUDE_USAGE_API`, `_CLAUDE_CREDENTIALS_FILE` | MED |
| `bterminal/ui/stats.py:124-130` | `_STATS_PRICING` z Claude model names | MED |
| `bterminal/ui/stats.py:215-325` | `SessionStatsBar` UI Claude-specific | HIGH |

**Abstrakcja:** Strategia + capability flag. `stats_bar` capability = false → bar nie tworzy się dla Copilota. Lub: provider-specific `StatsReader` interface (read_session_tokens, read_plan_usage).

### 3.4 AUTO-TRIGGER / IDLE (HIGH)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/ui/terminal_tab.py:587-650` | `_on_task_idle_timeout()` (#TODO Q21.3 obecne) | HIGH |
| `bterminal/ui/terminal_tab.py:111-141` | Idle timer setup tylko dla `claude_config` | MED |
| `bterminal/ui/terminal_tab.py:632-643` | `[AUTO-TRIGGER]` feed_child do VTE | MED |

**Abstrakcja:** Capability flag `task_auto_trigger`. Dla Copilota początkowo = false (idle detection trudniejsze). W przyszłości: parser `events.jsonl` + nowy idle handler.

### 3.5 INTRO PROMPT (MED)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/ui/dialogs/claude_code.py:38-78` | `_build_intro_prompt(project_name)` — markdown header "BTerminal — SSH/Claude terminal" | MED |
| `bterminal/helpers.py:104-154` | `_compute_intro_prompt_for_tab()` — plugin injection loop | LOW |

**Abstrakcja:** Header parametryzowany ("BTerminal — SSH/{provider} terminal"). Treść (ctx, tools, rules, plugins) identyczna. Plain text — działa wszędzie.

### 3.6 RULES INJECTION (MED)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/ui/terminal_tab.py:365-413` | `_maybe_inject_rules()` — interval logic w CTX DB | MED |
| `bterminal/ui/terminal_tab.py:415-483` | `_do_inject_rules`, `_do_inject_ctx_refresh` — `terminal.feed_child(...)` | MED |

**Abstrakcja:** Capability flag `rules_inject`. Dla Copilota: tak, działa identycznie (feed_child do PTY).

### 3.7 UI / DIALOG (HIGH)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/ui/dialogs/claude_code.py:81-220` | `ClaudeCodeDialog` z polami resume + skip_permissions | HIGH |

**Abstrakcja:** `AISessionDialog(provider)` — dynamiczne pola na podstawie `provider.dialog_schema`. Dropdown providera na górze.

### 3.8 SIDEBAR PANELS (LOW)

8 paneli — wszystkie provider-agnostic operacyjnie. Wyjątki:

- `ctx_manager.py` — używa `~/.claude-context/` ścieżki (nazwa katalogu, nie znaczenie). To path do BTerminala, nie do Claude'a.
- `tasks.py`, `consult.py`, `memory.py`, `git.py`, `plugin_manager.py`, `skills.py`, `files.py` — czysto provider-agnostic.

**Wniosek:** Paneli **nie trzeba zmieniać**. Są to UI okołoterminale, nie warstwa AI.

### 3.9 REST API / DEBUG (MED)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/debug_rest.py:496-521,978,988` | `/api/tabs/claude` endpoint | MED |
| `bterminal/debug_rest.py:1-100` | `record_feed()` labels: intro_prompt, auto_trigger, rules_inject, ctx_refresh | LOW |

**Abstrakcja:** Rename → `/api/tabs/ai/{provider}` (zachowując `/api/tabs/claude` jako alias przez 1 release).

### 3.10 PLUGIN RUNTIME (LOW)

`plugin_runtime.py`, `sidecar_runtime.py` są provider-agnostic. `BTerminalPlugin.get_session_context()` zwraca markdown wstrzykiwany do intro — działa identycznie dla każdego providera.

### 3.11 CONFIG / PATHS (LOW)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/config.py:36` | `CLAUDE_SESSIONS_FILE` | LOW |
| `bterminal/config.py:42-45` | `CTX_DB`, `CTX_IMAGES_DIR` w `~/.claude-context/` | LOW (nazwa katalogu, nie semantyka) |

### 3.12 TAB TITLE / VISUAL MARKER (MED)

| Plik:linia | Zjawisko | Trudność |
|---|---|---|
| `bterminal/app.py:678-689` | `_TAB_EMOJIS` hardcoded | MED |
| `bterminal/app.py:687` | Tab format `f"{base_name} #{count + 1} {emoji}"` | LOW |

**Abstrakcja:** R7a — emoji + kolor + short_label z `providers.json`.

### 3.13 REQUIREMENTS.md (PRE-PLANNED)

R4, R4a, R4b, R7a — strategia abstrakcji **już jest opisana** w docs/REQUIREMENTS.md (linie 158-334). Implementacja jeszcze nie istnieje. Dokument ten **nie wymaga aktualizacji** — jest zgodny z analizą.

---

## 4. Mapowanie capabilities (per-provider matrix)

Capability flag → wartość per provider. Bazuje na R4a + audycie Copilota.

| Capability | Claude | Copilot | Aider (future) | Notatka implementacyjna |
|---|---|---|---|---|
| `intro_prompt` | true | true (`-p` lub `-i`) | true | Copilot wymaga `-p "..."` (headless) lub `-i "..."` (interactive) |
| `resume_flag` | true (`--resume <id>`) | true (`--resume <id>`) | TBD | Identyczna semantyka |
| `continue_flag` | true (`--continue`) | true (`--continue`) | TBD | Identyczna semantyka |
| `skip_permissions` | `--dangerously-skip-permissions` | `--yolo` lub `--allow-all` | TBD | Mapping per provider |
| `granular_permissions` | false | true (`--allow-tool ...`) | TBD | UI advanced field tylko dla Copilota |
| `session_log` | true (single JSONL) | true (events.jsonl + SQLite) | false | Różny parser |
| `session_log_path` | `~/.claude/projects/{sanitized}/<uuid>.jsonl` | `~/.copilot/session-state/<uuid>/events.jsonl` | — | |
| `session_index_db` | false | true (`~/.copilot/session-store.db`) | false | Bonus dla Copilota: SQL session picker |
| `usage_api` | true (Anthropic OAuth) | false (brak publicznego API) | false | Stats bar % plan-usage tylko Claude |
| `usage_api_url` | `https://api.anthropic.com/api/oauth/usage` | — | — | |
| `cost_in_log` | true (`usage` per event) | true (`session.shutdown.modelMetrics.*.requests.cost`) | false | Cost calculator per provider |
| `pricing_table` | claude models hardcoded | (dynamic from events?) | — | Dla Copilota: skoro events zawierają cost — pricing table niepotrzebny |
| `rules_inject` | true | true (feed_child do PTY) | true | Identyczne |
| `task_auto_trigger` | true | **POCZĄTKOWO false** | TBD | Wymaga idle detection — Tier 3 |
| `stats_bar` | true | true (limited — bez plan-usage %) | false | Bar pokazuje się jeśli capability == true |
| `output_format_json` | `--output-format stream-json` | `--output-format json` | — | Inne nazwy, podobny semantyk |
| `plan_mode` | false (jest ExtThink) | true (`--plan` lub Shift+Tab) | TBD | UI: tylko Copilot |
| `autopilot` | false | true (`--autopilot --max-autopilot-continues N`) | TBD | UI: advanced field |
| `mcp_support` | true (`~/.claude.json mcpServers`) | true (`~/.copilot/mcp-config.json`) | false | BTerminal nie zarządza MCP — provider sam |
| `context_file` | `CLAUDE.md` (cumulative) | `AGENTS.md` (root only) | TBD | `_init_ctx_in_project_dir()` generuje oba |
| `oauth_creds_file` | `~/.claude/.credentials.json` | `~/.copilot/credentials.json` (?) | — | |

**Wniosek:** capability matrix pokrywa wszystkie różnice. Każda komponenta BTerminala czyta flagę i zachowuje się sensownie nawet jeśli flaga = false.

---

## 5. Architektura docelowa

### 5.1 Struktura plików (po implementacji)

```
bterminal/
├── providers/                    ← NOWY MODUŁ
│   ├── __init__.py               ← ProviderRegistry
│   ├── base.py                   ← AIProvider ABC + capabilities dataclass
│   ├── claude.py                 ← ClaudeProvider implementation
│   ├── copilot.py                ← CopilotProvider implementation
│   └── defaults.json             ← bundled default providers config
├── models.py
│   └── AISessionManager (rename z ClaudeSessionManager)
├── ui/
│   ├── stats/                    ← NOWY KATALOG (rename z stats.py)
│   │   ├── __init__.py
│   │   ├── base.py               ← AbstractStatsReader
│   │   ├── claude.py             ← ClaudeStatsReader (current logic)
│   │   ├── copilot.py            ← CopilotStatsReader (events.jsonl parser)
│   │   └── widget.py             ← SessionStatsBar GTK widget
│   ├── dialogs/
│   │   ├── ai_session.py         ← NOWY (rename z claude_code.py) + provider dropdown
│   │   └── claude_code.py        ← shim importing from ai_session.py (backward compat)
│   └── terminal_tab.py
│       └── spawn_ai_cli(provider, config)  (rename z spawn_claude)

~/.config/bterminal/
├── ai_sessions.json              ← rename z claude_sessions.json
├── claude_sessions.json.bak      ← backup z migracji
├── providers.json                ← user override defaults
└── options.json
```

### 5.2 Provider config schema (`providers.json`)

Bazuje na R4a + audycie Copilota. Pełen przykład:

```json
{
  "$schema": "1.0",
  "providers": {
    "claude": {
      "display": {
        "icon": "✨",
        "short_label": "Claude",
        "color": "#89b4fa",
        "long_label": "Claude Code"
      },
      "binary": {
        "search_paths": [
          "~/.local/bin/claude",
          "~/.npm-global/bin/claude",
          "/usr/local/bin/claude",
          "/usr/bin/claude",
          "/opt/homebrew/bin/claude",
          "~/.nvm/versions/node/*/bin/claude"
        ],
        "argv_prefix": []
      },
      "argv": {
        "intro_prompt_mode": "positional",
        "resume": ["--resume", "{session_id}"],
        "continue": ["--continue"],
        "yolo": ["--dangerously-skip-permissions"],
        "model": ["--model", "{model}"],
        "output_format_json": ["--output-format", "stream-json"]
      },
      "capabilities": {
        "intro_prompt": true,
        "resume_flag": true,
        "continue_flag": true,
        "skip_permissions": true,
        "granular_permissions": false,
        "session_log": true,
        "session_log_path": "~/.claude/projects/{sanitized_cwd}/{session_id}.jsonl",
        "session_index_db": false,
        "usage_api": true,
        "usage_api_url": "https://api.anthropic.com/api/oauth/usage",
        "oauth_creds_file": "~/.claude/.credentials.json",
        "cost_in_log": true,
        "rules_inject": true,
        "task_auto_trigger": true,
        "stats_bar": true,
        "plan_mode": false,
        "autopilot": false,
        "mcp_support": true,
        "context_file": "CLAUDE.md",
        "context_file_cumulative": true,
        "ready_marker": "system.stop",
        "default_model": "claude-sonnet-4-6"
      },
      "pricing": {
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_creation": 3.75, "cache_read": 0.30},
        "claude-opus-4-7": {"input": 15.0, "output": 60.0, "cache_creation": 18.75, "cache_read": 1.50},
        "claude-haiku-4-5": {"input": 0.80, "output": 4.0, "cache_creation": 1.0, "cache_read": 0.08}
      }
    },
    "copilot": {
      "display": {
        "icon": "🤖",
        "short_label": "Copilot",
        "color": "#a6e3a1",
        "long_label": "GitHub Copilot CLI"
      },
      "binary": {
        "search_paths": [
          "~/.local/bin/copilot",
          "~/.npm-global/bin/copilot",
          "/usr/local/bin/copilot",
          "/usr/bin/copilot",
          "/opt/homebrew/bin/copilot",
          "~/.nvm/versions/node/*/bin/copilot"
        ],
        "argv_prefix": []
      },
      "argv": {
        "intro_prompt_mode": "flag",
        "intro_prompt_flag": "-p",
        "intro_prompt_flag_interactive": "-i",
        "resume": ["--resume", "{session_id}"],
        "continue": ["--continue"],
        "yolo": ["--yolo"],
        "model": ["--model", "{model}"],
        "output_format_json": ["--output-format", "json"],
        "tui_safe": ["--no-banner", "--no-mouse", "--plain-diff", "--no-color"]
      },
      "capabilities": {
        "intro_prompt": true,
        "resume_flag": true,
        "continue_flag": true,
        "skip_permissions": true,
        "granular_permissions": true,
        "session_log": true,
        "session_log_path": "~/.copilot/session-state/{session_id}/events.jsonl",
        "session_index_db": true,
        "session_index_db_path": "~/.copilot/session-store.db",
        "usage_api": false,
        "cost_in_log": true,
        "rules_inject": true,
        "task_auto_trigger": false,
        "stats_bar": true,
        "stats_bar_no_plan_usage": true,
        "plan_mode": true,
        "autopilot": true,
        "mcp_support": true,
        "mcp_disable_flag": "--disable-builtin-mcps",
        "context_file": "AGENTS.md",
        "context_file_cumulative": false,
        "ready_marker": null,
        "ready_marker_strategy": "tail_events_jsonl",
        "default_model": "claude-sonnet-4-5"
      },
      "auth": {
        "env_vars": ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"],
        "interactive_command": "/login"
      }
    }
  },
  "default_provider": "claude"
}
```

### 5.3 Klasy core (high-level)

```python
# bterminal/providers/base.py
@dataclass
class ProviderCapabilities:
    intro_prompt: bool
    resume_flag: bool
    skip_permissions: bool
    session_log: bool
    session_log_path: str | None
    usage_api: bool
    rules_inject: bool
    task_auto_trigger: bool
    stats_bar: bool
    # ... wszystkie z 5.2

class AIProvider(ABC):
    name: str                              # "claude" | "copilot"
    display: ProviderDisplay               # icon, color, label
    capabilities: ProviderCapabilities
    pricing: dict | None

    @abstractmethod
    def find_binary(self) -> str | None: ...

    @abstractmethod
    def build_argv(self, config: dict, intro_prompt: str) -> list[str]: ...

    @abstractmethod
    def session_log_glob(self, project_dir: str) -> str: ...

    @abstractmethod
    def parse_session_log(self, path: str) -> SessionStats: ...

    def fetch_plan_usage(self) -> dict | None:
        return None  # override w Claude

    def detect_idle(self, terminal: Vte.Terminal, session_id: str) -> bool:
        # default: timeout-based; override w providerze ze stream-json
        ...

# bterminal/providers/__init__.py
class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, AIProvider] = {}

    def register(self, provider: AIProvider) -> None: ...
    def get(self, name: str) -> AIProvider: ...
    def all(self) -> list[AIProvider]: ...
    def load_from_config(self, config_path: Path) -> None: ...
```

### 5.4 Migracja sesji (R4b — implementacja)

```python
# bterminal/models.py
def _migrate_claude_to_ai_sessions():
    ai_path = Path(CONFIG_DIR / "ai_sessions.json")
    claude_path = Path(CONFIG_DIR / "claude_sessions.json")

    if ai_path.exists():
        return  # idempotent

    if not claude_path.exists():
        return  # nothing to migrate

    sessions = json.loads(claude_path.read_text())
    for s in sessions:
        s["provider"] = "claude"
        opts = {}
        if "resume" in s:
            opts["resume"] = s.pop("resume")
        if "skip_permissions" in s:
            opts["skip_permissions"] = s.pop("skip_permissions")
        if "sudo" in s:
            opts["sudo"] = s.pop("sudo")
        s["provider_options"] = opts

    ai_path.write_text(json.dumps(sessions, indent=2))
    claude_path.rename(claude_path.with_suffix(".json.bak"))
    print(f"[bterminal] Migrated {len(sessions)} sessions to ai_sessions.json")
```

---

## 6. Plan migracji etapowy

### Tier 1 — Foundation (krytyczne, blokuje resztę) — ~15 dni

- **T1.1** — Utwórz `bterminal/providers/{base,claude,copilot}.py` + `defaults.json`. ClaudeProvider ma 1:1 mapping istniejącego zachowania. CopilotProvider z capability flagami w większości false na początku.
- **T1.2** — Utwórz `ProviderRegistry` w `bterminal/providers/__init__.py`. Załaduj defaults + user override.
- **T1.3** — Rename `ClaudeSessionManager` → `AISessionManager`. Dodaj `provider` field. Backward-compat shim z importem.
- **T1.4** — Implementuj migrację `claude_sessions.json` → `ai_sessions.json` (R4b). Run on first boot. Test: 207 testów dalej passuje + 3 nowe testy migracji.
- **T1.5** — Wszystkie referencje `claude_config` → `ai_config` (z `ai_config["provider"]` lookup). Dla Tier 1: tylko Claude, żadna zmiana behawioru.

**Kryteria akceptacji T1:** Cały kod Claude działa identycznie. Pole `provider` istnieje w sesji. ProviderRegistry wykrywa providerów. Test suite zielony.

### Tier 2 — Spawn + UI dialog — ~25 dni

- **T2.1** — `spawn_claude` → `spawn_ai_cli(provider, config)`. Argv builder odczytuje z `provider.argv`. Test: stary `spawn_claude` zachowanie identyczne.
- **T2.2** — `_find_claude_path` → metoda `provider.find_binary()`. Single source of truth.
- **T2.3** — `ClaudeCodeDialog` → `AISessionDialog(provider)`. Pierwsze pole: dropdown `[Claude Code | GitHub Copilot]`. Pola providera dynamiczne na podstawie `provider.dialog_schema`.
- **T2.4** — Implementacja `CopilotProvider.build_argv()` z flagami `--no-banner --no-mouse --plain-diff -p "{intro}"` + auth env vars.
- **T2.5** — REST endpoint `POST /api/tabs/ai/{provider}` (alias `/api/tabs/claude` zachowany).
- **T2.6** — R7a: tab emoji + color z `provider.display`. Visual marker.
- **T2.7** — `_init_ctx_in_project_dir` generuje **i** `CLAUDE.md` **i** `AGENTS.md` (lub symlink).

**Kryteria akceptacji T2:** Można otworzyć sesję Copilot w tabie. Zakładka pokazuje 🤖 ikonę. Intro prompt dociera. Manualny smoke test.

### Tier 3 — Stats + Auto-trigger + Rules — ~20 dni

- **T3.1** — `SessionStatsBar` rozdzielony na strategy: `ClaudeStatsReader` (current) + `CopilotStatsReader` (events.jsonl parser). Capability flag `stats_bar` → bar pokazuje się tylko gdy true.
- **T3.2** — `_on_task_idle_timeout` z capability flag `task_auto_trigger`. Dla Copilota: początkowo `false` → BTerminal nie próbuje auto-trigger. Komunikat "Auto-trigger not supported for {provider}" w logach debug.
- **T3.3** — `_maybe_inject_rules` provider-aware (capability flag `rules_inject`). Dla Copilota: tak (feed_child działa identycznie).
- **T3.4** — Token tracking dla Copilota: parse `events.jsonl` → `session.shutdown.modelMetrics.*`. Pokaz w stats bar (bez plan-usage %).

**Kryteria akceptacji T3:** Stats bar działa dla Claude bez zmian. Stats bar dla Copilota pokazuje tokeny (bez plan %). Rules injection działa w obu. Auto-trigger działa tylko w Claude (placeholder for Copilot).

### Tier 4 — Polish + Copilot auto-trigger — ~15 dni

- **T4.1** — Implementacja Copilot idle detection przez tail-f `events.jsonl`. Włączenie `task_auto_trigger=true` dla Copilota.
- **T4.2** — Eksperyment z Copilot SQLite session picker (`session-store.db`) — nowa funkcja: w sidebarze sesji Copilota lista poprzednich sesji z FTS5 search (bonus).
- **T4.3** — Granular permissions UI dla Copilota (`--allow-tool` / `--deny-tool` advanced field).
- **T4.4** — Copilot plan mode toggle w dialogu sesji (Shift+Tab semantyka).
- **T4.5** — Cleanup: usuń backward-compat shimy po jednym release okresie.
- **T4.6** — Dokumentacja w README + screenshot z dwoma providerami.

**Kryteria akceptacji T4:** Pełna parity featurów Claude/Copilot. Test suite + visual demo.

### Tier 5 — Future (nie w zakresie) — Aider, ollama-local, OpenAI Codex

Dodanie kolejnych providerów = nowy plik `bterminal/providers/<name>.py` + entry w `providers.json`. Architektura gotowa.

---

## 7. Tools cross-provider compatibility

User wymaga: "tools jak CTX, Tasks, Consult, Memory, Skills, Files oraz Plugins działały dokładnie tak samo".

Analiza per-tool:

### 7.1 ctx (CTX context manager)
- **Mechanika:** SQLite DB w `~/.claude-context/`, CLI `tools/ctx`, sidebar panel `ctx_manager.py`.
- **Provider-specific?** NIE. To narzędzie BTerminala — operuje na własnej DB. Pozostaje 100% identyczne.
- **Intro prompt:** opis ctx jest częścią `_tools_help(project_name)` — tekst markdown wstrzykiwany identycznie do obu providerów.
- **Action item:** nazwa katalogu `.claude-context` jest historycznym artefaktem. Można ją zmienić na `.bterminal-context` (cosmetic, opcjonalnie w T5).

### 7.2 tasks (task management)
- **Mechanika:** SQLite DB w `~/.claude-context/tasks.db`, CLI `tools/tasks`, panel `tasks.py`.
- **Provider-specific?** NIE. Provider-agnostic.
- **Intro prompt:** sekcja "tasks" w `_tools_help` — identyczna.
- **Auto-trigger:** **JEST provider-specific** (idle detection). W T3.2 dla Copilota początkowo wyłączone, w T4.1 włączone gdy idle detection ready.

### 7.3 consult (multi-AI debate CLI)
- **Mechanika:** CLI `tools/consult`, panel `consult.py`. Provider niezależny.
- **Provider-specific?** NIE.
- **Intro prompt:** sekcja "consult" w `_tools_help` — identyczna.

### 7.4 memory (memory_wizard)
- **Mechanika:** CLI `tools/memory_wizard`, analizuje logi sesji → proponuje rules.
- **Provider-specific?** **TAK — częściowo.** Wizard parsuje JSONL Claude (`~/.claude/projects/...`). Dla Copilota musi parsować `~/.copilot/session-state/<uuid>/events.jsonl`.
- **Action item:** w T3 lub T4 — `memory_wizard` musi mieć `--provider {claude|copilot}` flag i odpowiedni parser. Albo: wizard detect provider z `ai_sessions.json`.
- **UI:** identyczne (intro prompt opisuje `memory_wizard <project>` — wizard sam wybierze parser).

### 7.5 skills (BTerminal skills panel)
- **Mechanika:** panel `skills.py` zarządza skills BTerminala (NIE skills providera).
- **Provider-specific?** NIE.
- **#TODO:** w `bterminal/ui/panels/skills.py` jest komentarz Q35.1 o per-provider `user_skills_dir` — to dotyczy skills providera w przyszłości. Na teraz: irrelevant.

### 7.6 files (files panel)
- **Mechanika:** panel `files.py` — przegląd plików project_dir.
- **Provider-specific?** NIE. Czyta filesystem.

### 7.7 plugins (BTerminal plugins)
- **Mechanika:** in-process kontrakt `BTerminalPlugin` + sidecar runtime. Plugin `get_session_context()` wstrzykuje markdown do intro.
- **Provider-specific?** NIE — kontrakt jest agent-agnostic. Plugin nie wie, jaki CLI go czyta.
- **UI per-tab override:** `enabled_plugins` w session config — działa identycznie dla obu providerów.

### 7.8 Podsumowanie

| Tool | Provider-specific? | Action items |
|---|---|---|
| ctx | NIE | none |
| tasks (DB ops) | NIE | none |
| tasks (auto-trigger) | TAK | T3.2 + T4.1 |
| consult | NIE | none |
| memory_wizard | TAK (parser) | T3/T4: dodaj `--provider` flag + Copilot parser |
| skills (BTerminal) | NIE | none |
| files | NIE | none |
| plugins | NIE | none |

**Wniosek:** 95 % toolingu jest provider-agnostic. Realna praca: tylko `memory_wizard` parser + auto-trigger idle detection.

---

## 8. Otwarte pytania (Q-blocks do decyzji użytkownika)

**Q-INT.1** — Czy Copilot CLI ma być wymagany z subskrypcją Copilot, czy BTerminal próbuje uruchomić go bez auth i pokazuje komunikat błędu? (Rekomendacja: try-and-show-error — same UX co Claude bez API key.)

**Q-INT.2** — Czy `_init_ctx_in_project_dir` ma generować osobny `AGENTS.md` dla Copilota, czy symlinkować `AGENTS.md → CLAUDE.md`? (Rekomendacja: symlink — DRY, single source of truth.)

**Q-INT.3** — Czy Copilot session picker (lista poprzednich sesji z `session-store.db`) jest priority? (Rekomendacja: bonus T4 — nie blokuje MVP.)

**Q-INT.4** — Czy nazwa katalogu `.claude-context` ma się zmienić na `.bterminal-context`? (Rekomendacja: TAK przy okazji T1, cosmetic ale czyściej.)

**Q-INT.5** — Czy MCP servers użytkownika mają być duplikowane między `~/.claude.json` a `~/.copilot/mcp-config.json`, czy zarządzane przez BTerminal w jednym miejscu? (Rekomendacja: punt do T5+ — niech provider sam zarządza, BTerminal tego nie dotyka.)

**Q-INT.6** — Czy w intro prompt informować model który provider go uruchomił? (Rekomendacja: NIE — provider sam wie, BTerminal wstrzykuje neutralny prompt.)

**Q-INT.7** — Default provider dla nowych sesji: `claude` czy ostatnio użyty? (Rekomendacja: ostatnio użyty, fallback `claude`.)

**Q-INT.8** — Czy wymagamy minimum Copilot CLI version (np. `2026.02+`, sprawdzane przy spawn)? (Rekomendacja: TAK — pokazuj błąd jeśli wersja < 2026.02 — wcześniej brak `--output-format json`.)

**Q-INT.9** — Czy Copilot `--mode plan` ma być eksponowany w GUI dialog? (Rekomendacja: T4 advanced field — NIE w MVP.)

**Q-INT.10** — Czy logi BTerminala mają zawierać typ providera w prefiksie? (Rekomendacja: TAK — `[claude] ...`, `[copilot] ...` w stderr.)

---

## 9. Estymacja effort + ryzyka

### 9.1 Effort

| Tier | Dni senior dev | Complexity |
|---|---|---|
| Tier 1 — Foundation | 15 | LOW (refactor + tests) |
| Tier 2 — Spawn + Dialog | 25 | MEDIUM (new UI, Copilot integration smoke) |
| Tier 3 — Stats + Rules | 20 | MEDIUM (parser, capability dispatching) |
| Tier 4 — Auto-trigger + Polish | 15 | HIGH (idle detection eksperymenty) |
| Tier 5 — Future providers | (per-provider) | LOW–MED |
| **Total MVP (T1–T3)** | **60 dni** | |
| **Total full parity (T1–T4)** | **75 dni** | |

### 9.2 Ryzyka

| Ryzyko | Prawdopodobieństwo | Impact | Mitigation |
|---|---|---|---|
| Copilot idle detection okazuje się niedeterministyczne | MED | HIGH | Fallback timeout-based; capability flag wyłącza auto-trigger |
| Copilot zmienia format `events.jsonl` w przyszłej wersji | MED | MED | Wersja minimum + parser z fallbackiem na `--output-format json` stdout |
| Copilot subscription required → user nie może testować bez płatnej subskrypcji | HIGH | LOW | Dokumentacja w README; mock_ai_cli scenarios dla testów |
| `--no-mouse --plain-diff --no-banner` nie wystarczają — TUI Copilota nadal psuje VTE | LOW | HIGH | Eksperyment w T2.4; fallback na `script(1)` wrapping lub dedicated PTY mode |
| Migracja `claude_sessions.json` → `ai_sessions.json` powoduje data loss przy edge case | LOW | HIGH | Backup `.bak` + idempotency test + migration smoke test w T1.4 |
| User skarży się że "stats nie działają" dla Copilota | MED | LOW | Capability flag wyłącza bar — user nie widzi pustego widgetu; tooltip "Plan usage not available for Copilot" |
| Plugins zaczynają zakładać Claude w `get_session_context()` | LOW | MED | Plugin contract docs aktualizować — agent-agnostic; review review obecnych pluginów |
| Granular permissions Copilota wymagają nowego UI complexity | MED | MED | Default `--yolo` jak Claude; UI advanced field w T4.3 |

### 9.3 Co testować (test plan high-level)

- **Unit (T1):** `ProviderRegistry`, `AISessionManager.migrate()`, capability flag lookup.
- **Component (T2):** `spawn_ai_cli` argv builder dla Claude i Copilot (mock binary). REST `/api/tabs/ai/{provider}`.
- **E2E (T2):** otworzyć tab Copilot przez REST; assert tab title ma 🤖.
- **E2E (T3):** stats bar widoczny dla Claude, niewidoczny dla Copilota gdy `stats_bar=false`; widoczny gdy `true`.
- **E2E (T4):** idle detection w Copilot — mock CLI emituje `events.jsonl`; auto-trigger fires.

---

## 10. Najważniejsze referencje

### Claude Code (oficjalne docs Anthropic)
- [Claude Code CLI Reference](https://code.claude.com/docs/en/cli-reference.md)
- [How Claude Code Works](https://code.claude.com/docs/en/how-claude-code-works.md)
- [Manage Sessions](https://code.claude.com/docs/en/sessions.md)
- [Hooks Reference](https://code.claude.com/docs/en/hooks.md)
- [Run Programmatically](https://code.claude.com/docs/en/headless.md)

### GitHub Copilot CLI (oficjalne docs GitHub)
- [GitHub Copilot CLI is now generally available — Changelog (2026-02-25)](https://github.blog/changelog/2026-02-25-github-copilot-cli-is-now-generally-available/)
- [About GitHub Copilot CLI — GitHub Docs](https://docs.github.com/copilot/concepts/agents/about-copilot-cli)
- [Installing GitHub Copilot CLI](https://docs.github.com/copilot/how-tos/set-up/install-copilot-cli)
- [Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
- [Allowing autonomous work (autopilot)](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/autopilot)
- [Allowing/denying tool use](https://docs.github.com/en/copilot/how-tos/copilot-cli/allowing-tools)
- [Using Copilot CLI session data (chronicle)](https://docs.github.com/en/copilot/how-tos/copilot-cli/chronicle)
- [github/copilot-cli (GitHub repo)](https://github.com/github/copilot-cli)
- [GitHub Copilot Plans & Pricing](https://github.com/features/copilot/plans)
- [Deprecation of gh-copilot extension (2025-09-25)](https://github.blog/changelog/2025-09-25-upcoming-deprecation-of-gh-copilot-cli-extension/)

### BTerminal — wewnętrzne dokumenty
- `docs/REQUIREMENTS.md` sekcje R4 / R4a / R4b / R7a / R8 / R8.f2 (linie 158-349)
- `docs/refactor-modular-architecture.md`
- `docs/test-coverage-matrix.md`
- `docs/plugin-spec.md`

---

## 11. Konkluzja i rekomendacja

**Rekomendacja:** Zacząć od **Tier 1 (15 dni)** jako foundation. Ten etap nie zmienia behawioru BTerminal, ale wprowadza warstwę abstrakcji, na której można bezpiecznie budować Tier 2+.

**Definition of done dla Tier 1:**
- [ ] `bterminal/providers/` istnieje z `base.py` + `claude.py` (1:1 z obecnym kodem)
- [ ] `providers/defaults.json` z full schema dla Claude + szkielet Copilot
- [ ] `AISessionManager` (rename z `ClaudeSessionManager`) z polem `provider`
- [ ] Migracja `claude_sessions.json` → `ai_sessions.json` (idempotent)
- [ ] 207 obecnych testów + 5 nowych (provider registry, capability flags, migration) — wszystkie zielone
- [ ] Zero zmian w UX dla użytkownika końcowego

**Po Tier 1** — Tier 2 (Copilot spawn) staje się względnie prosty: wystarczy dodać `copilot.py` z implementacją `build_argv` + `find_binary`, a cała reszta (sessions manager, dialogs, REST) już rozumie pojęcie providera.

**Ryzyko biznesowe odłożenia:** im dłużej kod siedzi w stanie Claude-only, tym więcej warstw zakłada Claude. Każdy nowy feature (np. R3a passwords w SessionDialog) tylko mocniej go zacementuje. **Tier 1 robić jak najszybciej.**

---

*Koniec dokumentu. Następny krok: decyzja użytkownika nt. otwartych pytań Q-INT.1 do Q-INT.10 + akceptacja planu Tier 1–4.*

---

## 12. Status implementacji — Implemented (2026-05-07)

**Tier 1 (foundation), Tier 2 (spawn + dialog + REST + visual marker), Tier 3 (stats split + capability dispatch + memory_wizard) i większość Tier 4 ZAMKNIĘTE.**

| Tier | Zadania | Status | Suite po tieru |
|------|---------|--------|----------------|
| Tier 1 (T1.1–T1.11) | provider ABC, defaults.json loader, ClaudeProvider, CopilotProvider skeleton, ProviderRegistry, AISessionManager rename, migracja `claude_sessions.json` → `ai_sessions.json`, intro prompt provider-aware, acceptance + coverage matrix update | ✅ | 505 passed |
| Tier 2 (T2.1–T2.12) | spawn_ai_cli dispatch, ClaudeProvider.build_argv finalize, CopilotProvider.build_argv (TUI-safe + intro modes), mock CLI scenario `copilot_basic.json`, AISessionDialog z dropdownem, get_dialog_schema, R7a visual marker (compute_tab_label), REST `/api/tabs/ai/{provider}`, AGENTS.md symlink, E2E open Copilot tab, install.sh detekcja Copilota, dual-provider acceptance | ✅ | 624 passed |
| Tier 3 (T3.1–T3.10) | SessionStatsBar split na strategy, CopilotStatsReader (events.jsonl), fixture copilot_events.jsonl + partial, Mock CLI emit_events_jsonl, capability dispatch dla stats_bar / task_auto_trigger / rules_inject, memory_wizard `--provider` + auto-detect, tokens-only widget, Tier 3 acceptance E2E | ✅ | 743 passed |
| Tier 4 (T4.1–T4.5) | Copilot idle detection (`_CopilotIdleMonitor` + `evaluate_idle_state`), task_auto_trigger flip, granular permissions UI (`allowed_tools` textarea + walidator), plan mode toggle (`--plan` checkbox), SQLite session-store reader (FTS5 search) | ✅ | 829 passed |
| Tier 4 (T4.7) | Documentation — README sekcja "AI providers", VM development workflow, ten "Implemented" wpis | ✅ | — |
| Tier 4 (T4.6, T4.8) | Cleanup shimów (deferred do T4.6.1), dual-provider workflow E2E acceptance | ⏳ open |
| Live follow-ups (L1–L3) | smoke argv, events.jsonl format verification, cost reporting | ⏸ blocked: Copilot subscription |
| Workflow (V1–V3) | V3 (VM test runner) ✅; V1 (updater bug fix) ⏳; V2 (host rsync sync) ⏳ | mixed |

**Capabilities Copilota po Tier 4 baseline:**
- ✅ True: `intro_prompt`, `resume_flag`, `continue_flag`, `skip_permissions`, `session_log`, `cost_in_log`, `stats_bar`, `stats_bar_no_plan_usage`, `rules_inject`, `task_auto_trigger`, `granular_permissions`, `plan_mode`.
- ❌ False (post-MVP / never): `usage_api` (no public Copilot endpoint), `autopilot`, `mcp_support`, `supports_sudo`.

**Nowe pure helpers (testowalne bez GTK), wszystkie w produkcji:**
- `bterminal.providers` — `load_providers_config`, `ProviderRegistry`, `get_registry`, `register_provider_class`, `reset_registry`.
- `bterminal.providers.claude.ClaudeProvider` — `build_argv`, `parse_session_stats`, `fetch_plan_usage`.
- `bterminal.providers.copilot.CopilotProvider` — `build_argv`, `get_dialog_schema`, `create_idle_monitor`, `evaluate_idle_state`.
- `bterminal.providers.copilot_session_store` — `CopilotSession`, `CopilotSessionStore.list_sessions / search / get_session`.
- `bterminal.ui.stats` — `create_stats_reader_for_ai_config`, `stats_widget_options_for_ai_config`.
- `bterminal.ui.terminal_tab` — `compute_tab_label`, `should_run_auto_trigger`, `should_inject_rules`.
- `bterminal.ui.dialogs.ai_session` — `is_valid_allowed_tool_rule`, `parse_allowed_tools_text`, `_split_provider_options_from_data`, `_flatten_session_for_legacy_dialog`.
- `tools/memory_wizard` — `_detect_provider_from_sessions`, `_resolve_provider`, `_build_ai_ask_argv`.

**Test suite po implementacji:** **829 passed** (z baseline 354 pre-Tier-1 → +475 nowych testów). Run-time fast suite ~13 s, pełna --slow ~30 s.

**Live verification follow-ups (L1–L3):** wymagają aktywnej subskrypcji GitHub Copilot. Po dostępie do `copilot` binary:
- L1: live smoke argv (czy `copilot --no-banner --no-mouse --plain-diff -p "..."` startuje bez błędów).
- L2: capture real `events.jsonl`, diff z `tests/fixtures/copilot_events.jsonl`, patch parser.
- L3: zweryfikuj format `session.shutdown.modelMetrics.*.requests.cost` (USD float vs PRU integer).

Komentarze `# TODO(L1)` / `# TODO(L2)` / `# TODO(L3)` rozsiane w kodzie wskazują dokładne miejsca do weryfikacji.

**Deployment workflow (po V3):**
- Host: `~/.local/share/bterminal/` zostaje na v1.3.0 (pre-Tier-1), nie przerywając pracy użytkownika.
- VM (`Mint_Michal` @ 192.168.0.123): rsync przez `tools/vm_sync.sh`, install + testy via `vm_install.sh` / `vm_test.sh`.
- Docelowy host deploy zaplanowany po V1 (updater bug fix) i V2 (rsync sync zamiast install.sh).
