# Audyt: dodanie 3rd providera (open-source) + Llama backend + GUI instalator

Data: 2026-05-07
Cel: oszacować realny koszt + istniejące "kostki" które można reuse.

## TL;DR

- **Provider abstraction jest gotowa** — nowy CLI to ~150 linijek nowej klasy + JSON entry. ABI przetestowane na 2 providerach.
- **OpenAI-compatible client już istnieje** w `tools/consult` (OpenRouter) — **kod do reuse** dla local LLM endpoint (ollama eksponuje OpenAI-compatible API).
- **Feature gating dziś jest ad-hoc** (8 callsite'ów `shutil.which()` w UI). #62 dał `diagnostics.DEPENDENCIES` registry — trzeba scentralizować przez nią.
- **GTK wizard pattern istnieje** — `CtxSetupWizard` (3 strony, Step N of 3 layout) — można forknąć jako baseline dla installer GUI.
- **Brak hardware probe** — żadnej introspekcji RAM/GPU/CPU w kodzie. Trzeba zbudować od zera (~80 linijek `psutil` + `nvidia-smi` + `/proc/cpuinfo`).
- **install.sh** to 630 linijek bash z 7 sekcjami; konwersja na GTK = ~400 linijek nowego kodu, **bash zostaje** dla CI/SSH/headless.

---

## 1. Provider abstraction (`bterminal/providers/`)

### Pliki
| Plik | Linijek | Rola |
|---|---|---|
| `base.py` | 188 | ABC `AIProvider` + dataclasses (`ProviderDisplay`, `ProviderCapabilities` z 24 polami, `SessionStats`) |
| `__init__.py` | 220 | `ProviderRegistry` + `load_providers_config()` (defaults.json + user override merge) + `get_registry()` singleton |
| `claude.py` | 270 | `ClaudeProvider(AIProvider)` |
| `copilot.py` | 470 | `CopilotProvider(AIProvider)` + `_CopilotIdleMonitor` + `evaluate_idle_state` |
| `copilot_session_store.py` | 200 | T4.5 SQLite session picker |
| `defaults.json` | 130 | bundled config dla obu providerów |

### Abstract methods (z `base.py:144-188`)
```python
@abstractmethod
def find_binary(self) -> Optional[str]                                  # binary lookup
@abstractmethod
def build_argv(self, config: dict, intro_prompt: str) -> list[str]      # spawn args
@abstractmethod
def session_log_glob(self, project_dir: str) -> Optional[str]           # log path
@abstractmethod
def parse_session_stats(self, log_path: str) -> SessionStats            # token/cost
def fetch_plan_usage(self) -> Optional[dict]                             # default None (Claude's OAuth)
def detect_idle(self, terminal, session_id, timeout_s=10.0) -> bool     # default True
def get_dialog_schema(self) -> list[tuple]                              # default empty
```

### 24 capability flags (w `base.py:74-109`)
intro_prompt, resume_flag, continue_flag, skip_permissions, granular_permissions, supports_sudo, session_log, session_log_path, session_index_db, session_index_db_path, usage_api, usage_api_url, oauth_creds_file, cost_in_log, rules_inject, task_auto_trigger, stats_bar, stats_bar_no_plan_usage, plan_mode, autopilot, mcp_support, context_file, context_file_cumulative, ready_marker, default_model

**Uwaga**: `mcp_support` i `autopilot` są zdefiniowane ale **nigdzie nie używane** w UI. Pierwszy real-use case = nowy provider z lokalnym LLM.

### Registry public API (z `__init__.py`)
```python
ProviderRegistry.get(name) -> AIProvider              # KeyError gdy unknown
ProviderRegistry.has(name) -> bool
ProviderRegistry.all() -> list[AIProvider]            # alfabetycznie
ProviderRegistry.default_provider() -> AIProvider     # z config["default_provider"]
get_registry() -> ProviderRegistry                    # singleton (lazy)
load_providers_config(user_path=None) -> dict         # bundled + user override deep-merge
reset_registry() -> None                              # test cleanup
```

### Dispatch surface — 31 callsite'ów w 8 plikach
`bterminal/{app,debug_rest,helpers}.py` + `bterminal/ui/{sidebar,terminal_tab,stats/__init__}.py` + `bterminal/ui/dialogs/ai_session.py` + sam `providers/__init__.py`

**Implikacja**: nowy provider = 0 zmian w callsitach (registry handles dispatch). Wszystkie 31 miejsc działa identycznie dla N providerów.

### Wnioski dla Faza 0
Nowy provider = **1 nowy plik klasy** (`providers/<name>.py`, ~150 linijek na bazie `claude.py` jako wzór bo prostszy niż copilot.py) + **1 sekcja w defaults.json** (~30 linijek). Build_argv + parse_session_stats to jedyne real-implementation, reszta ma defaults.

---

## 2. Existing OpenAI-compatible client (consult tool)

`/home/bartek/.local/bin/consult` (Python CLI, ~30KB) ma:
- `call_openrouter(api_key, model, system, user)` — pełny chat-completion request flow (HTTPS + JSON streaming + retry)
- Multi-model debate orchestration (Tribunal feature)
- Model registry z enabled/disabled toggle (`~/.config/bterminal/consult.json`)

**OpenRouter używa OpenAI-compatible API** (chat/completions endpoint, identyczny shape jak ollama, vLLM, llama.cpp `--api-key dummy`). Czyli **logika requestu jest 1:1 reusable** — wystarczy zamienić `OPENROUTER_API` na `http://localhost:11434/v1/chat/completions`.

### Implikacja dla local LLM
Jeśli dodajemy nowy provider który komunikuje się z lokalnym LLM przez REST (ollama / llama.cpp serve / vLLM), **NIE potrzebujemy nowej klasy klienta HTTP**. Wystarczy refactor `call_openrouter` w consult na `call_openai_compatible(base_url, api_key, model, ...)` i provider używa go z `base_url=http://localhost:11434/v1`.

---

## 3. Feature gating w UI dziś (ad-hoc — TRZEBA SCENTRALIZOWAĆ)

8 miejsc w UI używa `shutil.which()` lub bezpośredniego sprawdzania binarki:

| Plik:linia | Co sprawdza | Co robi gdy missing |
|---|---|---|
| `ui/panels/files.py:108,257,292,329,386` | `meld`, `xdg-open` | `set_sensitive(False)` na przyciskach |
| `ui/panels/files.py:500` | `xdg-open` lub generic | conditional menu item |
| `ui/sidebar.py:934` | `command` | conditional context menu entry |
| `ui/panels/consult.py:646` | (rule-based) | sensitive toggle |

**Problem**: każdy callsite duplikuje logic. `diagnostics.DEPENDENCIES` z #62 ma już `present` field per dep + `feature` description. Gating powinien iść przez **jeden helper** w `bterminal/diagnostics.py`:

```python
def is_feature_available(cmd: str) -> bool:
    """Single-source feature check used by UI gates. Wrapper around
    detect_tool() with a 60s cache so refresh isn't an apt-list cost."""
```

To wymaga refactoru 8 callsite'ów na `is_feature_available("meld")`. Plus **runtime cache invalidation** gdy installer GUI kończy install (signal "deps changed, refresh gating").

---

## 4. GTK Wizard pattern — `CtxSetupWizard`

`bterminal/ctx/dialogs.py:43-380` — `class CtxSetupWizard(Gtk.Dialog)` z **3-step layout**:
- `_build_page_project()` — Step 1 of 3
- `_build_page_entry()` — Step 2 of 3
- `_build_page_confirm()` — Step 3 of 3

State machine z next/back buttons + page swapping przez Gtk.Stack lub manual show/hide widget'ów. Wzór gotowy do **forknięcia jako `InstallerWizard`** z 5 stronami:
1. Welcome + license accept (już istnieje `_show_license_dialog` w `license.py`, do reuse)
2. Dependency detection (live `diagnostics.audit()` table)
3. User picks: meld / git-lfs / llama / poppler / pandoc / latex (checkboxes per `auto`/`optional` tier)
4. Install progress (log streaming z bash subprocess `install.sh --headless --selected meld,llama,...`)
5. Summary + open BT

**KEY insight**: GTK installer **nie zastępuje bash**, tylko go orkiestruje. `install.sh` przyjmuje `--headless --selected <csv>` i robi pracę. Bash zostaje canonical (CI, Vagrant, SSH-over-X11-forwarding without GTK), GTK jest UX overlay.

---

## 5. Brak hardware probe

`grep -rn "psutil|gpu|cuda|nvidia-smi|/proc/cpuinfo|RAM|memory" bterminal/ --include="*.py"` zwraca **0 hits** związanych z probe. Wszystkie matche to in-memory password cache (R3a).

**Implikacja**: 100% nowy moduł `bterminal/system_probe.py`:
```python
def probe_system() -> dict:
    """Returns:
      {
        "ram_gb": 16.0,                    # from psutil.virtual_memory()
        "cpu_cores": 8,                    # os.cpu_count()
        "cpu_avx2": bool,                  # /proc/cpuinfo flags
        "cpu_avx512": bool,
        "gpu_nvidia": [{"name": "RTX 3060", "vram_gb": 12}, ...],  # nvidia-smi
        "gpu_amd": [...],                  # rocm-smi or /sys/class/drm/
        "disk_free_gb": 250.0,             # for ~/.cache/llama models
        "ollama_installed": bool,
        "llamacpp_installed": bool,
      }
    """

def recommend_models(probe: dict) -> list[str]:
    """Pick models the user can actually run.

    Heuristic:
      ram_gb < 4   → 0.5B Q4 only (Qwen-Coder 0.5B, ~400MB)
      ram_gb < 8   → 1B-3B (TinyLlama, Phi-3-mini)
      ram_gb < 16  → 7B Q4 (Llama-3-8B, Qwen-Coder-7B)
      gpu_vram_gb >= 12 → 13-14B Q5 (Llama-3-13B)
      gpu_vram_gb >= 24 → 30B+ (Qwen-Coder-32B)
    """
```

`psutil` jest już ALMOST in deps — `defaults/dependencies.json` go nie listuje. Trzeba dodać.

---

## 6. install.sh — 630 linijek, 7 sekcji

| Sekcja | Linijki | Co robi |
|---|---|---|
| [1/7] Runtime | 94-144 | Python ≥3.10, Node ≥22, npm |
| [2/7] Claude Code | 145-199 | npm install -g @anthropic-ai/claude-code |
| [2.5/7] Copilot | 200-278 | npm install -g @github/copilot (#64 — auto-install) |
| [3/7] System tools | 279-373 | check_tool flow (#62) — git/ssh required, meld/pandoc/latex auto |
| [4/7] GTK bindings | 374-399 | python3-gi, gir1.2-{gtk-3.0,vte-2.91} via apt |
| [5/7] Files | 400-543 | rsync to ~/.local/share/bterminal/ + rollback |
| [6/7] Symlinks | 544-554 | bin/* → install_dir |
| [7/7] Init ctx | 555-630 | desktop entry, ctx setup |

`TOOL_REPORT[]` array (#62) tracks per-tool result, `emit_tool_summary` emit'uje `[SUMMARY]` block.

### Co dodać dla Llama
Nowa sekcja **[2.7/7] Local LLM backend** (między Copilot a System tools):
- Sprawdzić czy `ollama` jest installed (`command -v ollama`)
- Jeśli nie + user opt-in (flag `--with-llama`): `curl -fsSL https://ollama.com/install.sh | sh`
- (opcjonalnie) `ollama pull qwen2.5-coder:0.5b` jako test model
- Push do `TOOL_REPORT` jako `auto` tier z feature description

### Co dodać dla GTK orchestrator
- Flag `--headless --selected meld,llama,latex` parser (po `--no-sudo`)
- Każde `check_tool` consultuje `--selected` whitelist; "auto" deps nie listed = skip install attempt
- `--gtk-mode` (lub `--from-gtk-wizard`) — emit machine-readable JSON status na każdym phase za pomocą `[STATUS] {"phase": "claude", "status": "installing"}` linie. GTK side parsuje stdin live.

---

## 7. Inventory — co jest dziś vs co trzeba dodać

| Obszar | Dziś | Brakuje |
|---|---|---|
| Provider class | claude.py + copilot.py | **3rd plik** (~150 linijek) |
| Defaults config | defaults.json (2 providery) | **3-cia sekcja** + 1 nowy capability `local_endpoint_url` |
| Dependency detect | diagnostics.DEPENDENCIES (#62, 9 deps) | **+ ollama / llamacpp / psutil / nvidia-smi** w registry |
| OpenAI-compat HTTP client | `consult` tool | **wyciągnąć do `bterminal/openai_compat.py`** + reuse |
| Feature gating | 8 ad-hoc callsite'ów | **scentralizować w diagnostics.is_feature_available()** + invalidate hook |
| GTK wizard pattern | CtxSetupWizard (3 strony) | **InstallerWizard (5 stron)** fork |
| Hardware probe | nic | **`system_probe.py`** od zera |
| Model manager UI | nic | **`OptionsDialog` rozszerzenie** + ModelRegistry persist |
| Installer UX | bash CLI | **GTK orchestrator** + bash `--headless` mode |

---

## 8. Decyzje architektoniczne wymagające input

(Te 3 decyzje blokują finałowe rozpisanie tasków — defaults zaproponowane, do akceptacji.)

### Q1 — Który open-source CLI dodać jako 3rd provider?
| Kandydat | Stars | Język | Argumenty |
|---|---|---|---|
| **Aider** ⭐ | 12k | Python | OpenAI-compatible (działa z ollama out-of-box), git-aware, dojrzały, `--model openai/qwen` lub `--model ollama/qwen` |
| Goose (Block) | 5k | Rust | MCP-native, agent loop, młodszy ale aktywny |
| OpenAI Codex CLI | 8k | TypeScript/npm | open-sourced 2026, similar UX do Claude Code |
| gptme | 3k | Python | minimalistyczny |

**User wpisał "Cursor"** — Cursor to closed-source komercyjny IDE (free tier ma `cursor-agent` CLI ale nie open-source). Najbliższy spirit: Aider.

### Q2 — Llama backend?
| Opcja | Pro | Con |
|---|---|---|
| **Ollama** ⭐ | 1-line install, OpenAI-compat REST, model library + auto-quantize, daemon zarządza modelami | Wymaga oddzielnego daemon'a (port 11434) |
| llama.cpp + serve | max kontrola, raw GGUF | więcej boilerplate (manual model download, manual quant pick) |
| vLLM | production-grade throughput | wymaga GPU + skomplikowany install + Python ≥3.11 |

### Q3 — Tester model dla "podpięcia eksperymentalnego"?
| Model | Rozmiar (Q4) | RAM min | Quality |
|---|---|---|---|
| **Qwen2.5-Coder-0.5B** ⭐ | 400MB | 1GB | code-tuned tiny, działa na każdym sprzęcie |
| TinyLlama-1.1B | 700MB | 2GB | generic chat, słabsze code |
| Phi-3-mini-4k | 2.4GB | 4GB | dobry quality dla rozmiaru |
| Qwen2.5-Coder-7B | 4.5GB | 8GB | znacznie lepszy code, wymaga średni sprzęt |

---

## 9. Plan rozpisania zadań (po Q1-Q3)

12 zadań w 4 fazach. Każde ≤4h, każde z manual test steps + automated tests.

### Faza 0 — Foundation (3 tasks, ~8h)
- **#73** `system_probe.py` + `recommend_models()` + tests (RAM/GPU/CPU detection)
- **#74** `bterminal/openai_compat.py` (refactor `consult.call_openrouter` → reusable client)
- **#75** Nowy provider class `bterminal/providers/<name>.py` + defaults.json sekcja + capability `local_endpoint_url`

### Faza 1 — GUI Installer (3 tasks, ~12h)
- **#76** `install.sh --headless --selected csv --status-json` + parity tests
- **#77** `bterminal/ui/installer_wizard.py` (5 pages na bazie CtxSetupWizard) + log streaming widget
- **#78** Wire `install.sh` jako entry point z `--gtk` flagą → spawn wizard

### Faza 2 — Model manager + feature gating (3 tasks, ~8h)
- **#79** OptionsDialog "Models" sekcja: download/switch/delete dla ollama models + recommendations panel z `recommend_models(probe())`
- **#80** Centralize feature gating w `diagnostics.is_feature_available()` + 8 callsite refactor
- **#81** Auto-invalidate gating po install completion (signal hook)

### Faza 3 — Polish + integration (3 tasks, ~6h)
- **#82** README "Local LLM" sekcja z hardware matrix table
- **#83** ProviderManager UI: enable/disable provider w Options (skoro 3 providery, user może chcieć ukryć dropdown'a entries)
- **#84** Manual smoke checklist na czystej VM (Vagrantfile?) — runbook do zaakceptowania jako "release-ready"

### Tests strategy (per task)
- Pure helpers: pytest unit tests, mock subprocess/psutil/nvidia-smi
- GTK widgets: pytest pod xvfb-run (jak `tests/test_ai_session_dialog_widgets.py`)
- Installer wizard: subprocess fixture spawnujący BT z `--installer-test-mode` env (bypass real apt)
- Manual smoke: każdy task ma "Manual test:" sekcję z konkretnymi krokami na VM

---

## 10. Pułapki + ryzyka

1. **Ollama daemon lifecycle** — jeśli BT spawnuje sesję która używa local LLM, czy ollama serve musi już działać? Albo BT auto-startuje? Albo systemd unit? **Decyzja**: BT sprawdza `curl :11434/api/tags`, jeśli down → user prompt "ollama serve nie działa, uruchomić w background?" + `nohup ollama serve &`.

2. **Hardware probe na non-Linux** — `nvidia-smi` może być nieobecne, `/proc/cpuinfo` to Linux-only. Probe musi być **defensywny** — fallback do `cpu_count()` + `psutil.virtual_memory()` zawsze działają.

3. **Model download size** — Qwen-Coder-7B Q4 to 4.5GB. Wizard MUSI pokazywać download progress + cancel. Nie hardcoded — przez ollama `pull` parsing stdout.

4. **Aider vs Claude Code intro_prompt** — Aider nie ma wbudowanego concept "intro prompt at session start" (zaczyna z prosty repl). Adaptacja: BT może `tab.terminal.feed_child(intro_prompt + "\n")` po idle (tak jak rules_inject — ten path już istnieje).

5. **Aider session log** — Aider zapisuje conversation w `.aider.chat.history.md` w project_dir. Format markdown, nie JSONL — `parse_session_stats` musi liczyć tokens differently (regex lub LLM count). Albo capability `cost_in_log: false` + UI wyświetla "n/a".

6. **GTK installer + sudo** — apt install meld wymaga sudo. Wizard nie może embed pkexec dialog czysto. Best: spawn `pkexec install.sh --headless --selected ...` żeby polkit handlował password prompt.

---

## Następny krok

Twoja decyzja na Q1-Q3 (wystarczy 3 słowa: `Aider, Ollama, Qwen`) → rozpisuję wszystkie 12 tasków w `tasks` queue z konkretnymi referencjami line:line na ten audyt + manual test plan per task.

Bez tej decyzji zacznę z domyślnymi (Aider + Ollama + Qwen2.5-Coder-0.5B) ale każdy z 12 tasków ma "TODO(provider-decision)" w opisie i pierwszy task #73 to puro `system_probe.py` które jest niezależne od wyboru.
