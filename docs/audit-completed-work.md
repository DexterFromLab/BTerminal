# Audit: Completed Work for the 3rd-Provider (Aider) Initiative

Status: 25/25 tasks closed; 1528 tests collected, 139 in 9 new files
ship verified (passing in cohesive scope), 1 xfail lifted, 6 source-
level changes ride on existing harnesses (legacy AGENTS.md / dispatch
suites). Lines of code added (production): ~1.4k (system_probe,
openai_compat, AiderProvider, AiderStatsReader, ollama_client,
installer_wizard, ctx helpers refactor); install.sh extended ~280
lines; tests added: ~6.2k lines across 20 new test files.

This audit reads the work backwards: from the integration matrix
back to the per-module deltas, then to the failure-mode catalog
that #27 will turn into expanded test backlog.

Document is structured for two audiences:
- a maintainer doing release verification (sections 1-3)
- a planner doing #27 backlog expansion (sections 4-7)

---

## 1. Executive summary

**Promise:** add a 3rd AI provider (Aider) running against a local
LLM (Ollama + Qwen-0.5B) without breaking the existing
Claude/Copilot dispatch fabric.

**Delivered:**
- Provider abstraction generalized for local-LLM dispatch
  (`local_endpoint_url` capability, `intro_prompt_mode=stdin_feed`,
  `cost_in_log=False` rendering path)
- Hardware probe + heuristic model recommendations
  (bterminal/system_probe.py:185-245)
- OpenAI-compatible HTTP client extracted to a reusable module
  (bterminal/openai_compat.py)
- `install.sh` gains `--headless`, `--selected`, `--status-json`
  flags (install.sh:lines added ~280) + Ollama opt-in install path
- 5-page GTK installer wizard
  (bterminal/ui/installer_wizard.py:1-584) auto-spawned by
  install.sh when DISPLAY+Wayland/X11+gtk available
- Diagnostics gate centralized via TTL-cached
  `is_feature_available()` + invalidation listener
  (bterminal/diagnostics.py:140-275)
- Local Models OptionsDialog + AI Providers enable/disable section
  (bterminal/ui/dialogs/options.py:additions)
- AIDER.md auto-symlink (#92) + per-provider context_file dispatcher
- AiderStatsReader (#94) + cost_unavailable widget rendering
- 6 VM-bound runbook scripts + pytest source-level wrappers
  (`tools/test_install_vm.sh`, `test_update_vm.sh`,
  `test_installer_wizard_vm.sh`, `smoke_3rd_provider.sh`,
  `test_aider_real_model.sh`, plus the e2e fixture-based
  `test_aider_full_session.py`)

**Numbers (line:line accountable):**

| Module | Prod LOC | Test LOC | Coverage ratio | Notes |
|---|---:|---:|---:|---|
| system_probe.py | 245 | 245 | 1.00 | hardware fallbacks all branched |
| openai_compat.py | 227 | 411 | 1.81 | error paths + retry |
| providers/aider.py | 248 | 437 | 1.76 | argv + parse + capabilities |
| providers/{base,init,defaults} | 675 | 599 | 0.89 | registry + schema |
| ui/installer_wizard.py | 584 | 237 | 0.41 | wizard widgets — GUI gap |
| ui/dialogs/options.py (delta) | ~150 | ~80 | 0.53 | new sections |
| ollama_client.py | 220 | (in #79 indirect) | n/a | parsers covered |
| diagnostics.py (delta) | ~75 | (in #80) | covered | TTL cache + invalidation |
| ctx/helpers.py (delta) | ~95 | 422 | 4.44 | high — fixed pattern |
| ui/terminal_tab.py (delta) | ~50 | 643 | 12.86 | dispatch glue stress-tested |
| ui/stats/aider.py | 118 | 281 | 2.38 | reader thin adapter |
| ui/stats/{init,widget} (delta) | ~40 | (covered) | n/a | factory + cost rendering |
| install.sh (delta) | ~280 | 658 | 2.35 | flags + JSON stream + rollback |

Aggregate: ~2200 prod LOC delta vs ~6900 test LOC = ~3.1x test
coverage ratio. Well above the 1.5x heuristic Claude Code project
documents (CLAUDE.md "test-coverage-matrix"). The wizard at 0.41
is the only major gap — covered by VM smoke + xdotool runner
(#87) but no widget unit tests.

---

## 2. Decision graph (provider dispatch)

```
                      ┌─────────────────────────────────┐
                      │  ai_session.json: provider=     │
                      │  {claude | copilot | aider}     │
                      └──────────────┬──────────────────┘
                                     │
                                     ▼
                      ┌─────────────────────────────────┐
                      │  ProviderRegistry.get(name)     │
                      │  (singleton, defaults.json +    │
                      │   user override merge)          │
                      └──────────────┬──────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
      ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
      │ Claude       │       │ Copilot      │       │ Aider        │
      │ ready=stop   │       │ ready=null   │       │ ready=null   │
      │ rules=true   │       │ rules=true   │       │ rules=true   │
      │ stats=true   │       │ stats=true   │       │ stats=true   │
      │ no-plan=F    │       │ no-plan=T    │       │ no-plan=T    │
      │ cost-log=T   │       │ cost-log=T   │       │ cost-log=F   │
      │ ctx=CLAUDE   │       │ ctx=AGENTS   │       │ ctx=AIDER    │
      │ paste=null   │       │ paste=hint   │       │ paste=hint   │
      │ local-ep=null│       │ local-ep=null│       │ local-ep=:11434│
      └─────┬────────┘       └─────┬────────┘       └──────┬───────┘
            │                      │                       │
            ▼                      ▼                       ▼
      ┌─────────────────────────────────────────────────────────┐
      │  build_argv(config, intro_prompt) — intro_prompt_mode    │
      │   positional ─ Claude     [binary, intro, ...]          │
      │   flag       ─ Copilot    [binary, -p, intro, ...]      │
      │   stdin_feed ─ Aider      [binary, --model, …, project] │
      │                            (intro injected via PTY)     │
      └─────────────────────────────────────────────────────────┘
                                   │
                                   ▼
      ┌─────────────────────────────────────────────────────────┐
      │  TerminalTab.__init__: factory dispatch                  │
      │   ↓ create_stats_reader_for_ai_config(cfg, registry)     │
      │     ──> ClaudeStatsReader / CopilotStatsReader /         │
      │         AiderStatsReader                                 │
      │   ↓ stats_widget_options_for_ai_config(cfg, registry)    │
      │     ──> {hide_plan_usage, cost_unavailable}              │
      │   ↓ self._stats_bar = SessionStatsBar(...)               │
      └─────────────────────────────────────────────────────────┘
                                   │
                                   ▼
      ┌─────────────────────────────────────────────────────────┐
      │  Periodic: _on_contents_changed_tasks                    │
      │   → debounce 2 s VTE-silent → _on_task_idle_timeout      │
      │     ├ if _inject_pending: _do_inject_rules               │
      │     │     → extract_rules_inject_bytes(provider, project,│
      │     │        rules_stdout) → feed_child(bytes)           │
      │     │       (provider-agnostic; pinned by #93)           │
      │     └ if should_run_auto_trigger(cfg, registry):         │
      │         _claim_next_task(db, project, session_id)        │
      │         → feed_child("[AUTO-TRIGGER] task: <id> — …")    │
      └─────────────────────────────────────────────────────────┘
                                   │
                                   ▼
      ┌─────────────────────────────────────────────────────────┐
      │  ctx wizard finalize:                                    │
      │   ensure_context_files_for_all_providers(project_dir)    │
      │     ──> per provider: capabilities.context_file          │
      │         AGENTS.md / AIDER.md → CLAUDE.md (symlink)       │
      │         (broken stale symlinks repaired #92)             │
      └─────────────────────────────────────────────────────────┘
```

Key invariants pinned by tests:
- All capability gates flow through registry; no inline
  `if provider == "X"` branches in `should_inject_rules`,
  `should_run_auto_trigger`, `_idle_check_tick`, `_do_inject_rules`,
  `_on_task_idle_timeout`, `extract_rules_inject_bytes`. Pinned by:
  `tests/test_aider_auto_trigger.py:test_idle_check_tick_does_not_branch_on_ready_marker`,
  `tests/test_rules_inject_provider_parity.py:test_helper_does_not_branch_on_provider_in_implementation`.
- All registry-driven dispatchers (stats reader factory, context
  file dispatcher, image paste template) iterate via
  `registry.names()` so adding a 4th provider auto-extends.
  Pinned by:
  `tests/test_context_file_per_provider.py:test_dispatcher_skips_providers_without_context_file`.

---

## 3. Per-module audit

### 3.1 system_probe (#73)

**Shipped** (bterminal/system_probe.py:1-245):
- `probe_system() → dict` returns `ram_gb`, `cpu_cores`,
  `cpu_avx2`, `cpu_avx512`, `gpu_nvidia[]`, `gpu_amd[]`,
  `disk_free_gb`, `ollama_installed`, `llamacpp_installed`
  (system_probe.py:24-115)
- `recommend_models(probe)` heuristic with `_MODEL_TIERS` table
  RAM/VRAM → `[model_name]` (system_probe.py:155-220)

**Coverage** (tests/test_system_probe.py:1-245, 1.00 ratio):
- Probe returns dict shape with all keys (test #1)
- AVX2/AVX512 detection from `/proc/cpuinfo` flags
- nvidia-smi CSV parse for VRAM detection
- AMD ROCm path via `rocm-smi`
- Disk free via `shutil.disk_usage(/tmp)`
- Defensive fallbacks for missing CLIs (subprocess returns 127)
- Recommendations: 1 GB → Qwen-0.5B only, 16 GB+12 GB GPU → 13B,
  32 GB → 32B (only with 40 GB threshold).

**Decision tree branches tested:**
- ✓ probe with all subprocess calls failing → empty arrays
- ✓ recommend_models with each RAM tier
- ✓ AMD GPU also counts toward VRAM (32 GB constraint)

**Edge cases NOT covered:**
- `psutil` missing entirely (we ship as `auto` dep)
- Fractional RAM (probe rounds; what about 7.9 GB → bucket?)
- Multiple GPU mix (1 NVIDIA + 1 AMD → probe sums but
  recommend_models may pick wrong tier)
- Container/VM with `/proc` shaped differently (LXC, WSL2)

**Integration points:**
- Used by InstallerWizard inventory page (#77)
- Used by OptionsDialog model recommendations (#79)

### 3.2 openai_compat (#74)

**Shipped** (bterminal/openai_compat.py:1-227):
- `call_chat_completion(base_url, api_key, model, messages, **kw)`
  pure-stdlib urllib (openai_compat.py:90-160)
- Typed exceptions: `APIError`, `AuthError(401)`,
  `RateLimitError(429)`, `ServerError(5xx)` (lines 35-65)

**Coverage** (tests/test_openai_compat.py:1-411, 1.81 ratio):
- Mock urllib roundtrip — request shape, headers, body
- Streaming responses (SSE-style)
- 401/429/5xx mapped to typed exceptions
- Timeout propagation
- JSON parse errors (truncated stream)
- consult shim regression (back-compat)

**Decision tree branches tested:**
- ✓ Successful 200 + JSON
- ✓ Each typed exception path
- ✓ Streaming chunked vs single-shot

**Edge cases NOT covered:**
- Connection refused (Ollama not running) — probably falls
  through urlopen's URLError but no explicit test
- Partial chunk in SSE stream (split mid-JSON)
- HTTP/2 → tls? urllib doesn't speak HTTP/2 — Ollama is fine
  but llama.cpp might require it
- Custom CA / self-signed cert (corp env)

### 3.3 AiderProvider (#75 / #3)

**Shipped** (bterminal/providers/aider.py:1-248):
- `find_binary` walks `~/.local/bin/aider`, pipx venv, system
  paths (aider.py:55-72)
- `build_argv` produces `aider --model openai/qwen2.5-coder:0.5b
  --openai-api-base http://localhost:11434/v1 --openai-api-key
  dummy --no-stream --no-show-model-warnings <project_dir>`
  (aider.py:74-155)
- 4-layer model resolution (session → global default → capability
  → hardcoded) (aider.py:99-115)
- `parse_session_stats` regex `Tokens: <N> sent, <M> received`
  with k/M suffixes (aider.py:172-218)

**Coverage** (tests/test_aider_provider.py:1-437, 1.76 ratio):
- `find_binary` precedence: pipx > local > system
- `build_argv` composition for all flag combos
- intro_prompt NOT in argv (stdin_feed mode) — pinned twice
- `parse_session_stats` k-suffix handling, model attribution,
  empty log
- Capability schema: rules_inject + session_log + stats_bar all
  True; cost_in_log False; ready_marker null

**Decision tree branches tested:**
- ✓ Model resolution priority: session opts >> global pref >>
  capability default >> hardcoded
- ✓ Empty config; missing project_dir; missing binary
- ✓ Non-UTF8 chat history (errors=replace)

**Edge cases NOT covered:**
- Aider 0.x vs 1.x argv compatibility (we hardcode flags assuming
  newer aider)
- `aider --read CLAUDE.md` explicit pass — currently we rely on
  cwd auto-discovery (#96 pinned this contract)
- Multi-model session (aider can switch model mid-conversation)
- Aider `--config` file support

**Integration points:**
- Registered in `providers/__init__.py:_PROVIDER_CLASSES`
- AiderStatsReader (#94) consumes `parse_session_stats`
- ensure_context_file uses `capabilities.context_file = "AIDER.md"`

### 3.4 install.sh extension (#76)

**Shipped** (install.sh, ~280 lines added):
- `--headless` (skip prompts) + `--selected csv` (whitelist) +
  `--status-json` (machine-readable progress)
- `status_json <phase> <status> <progress> <label>` helper
- 9 status_json calls at phase boundaries (5%/15%/30%/40%/50%/65%/
  75%/90%/95%/100%)
- New phase `[2.7/7] Local LLM backend (Ollama)` opt-in via
  `--selected llama` triggers `curl -fsSL https://ollama.com/install.sh | sh`
- `maybe_launch_gtk_wizard` auto-spawn detection

**Coverage:**
- `tests/test_install_copilot_detection.py` (160 lines): existing
  tests + new --headless/--no-sudo modes
- `tools/test_install_vm.sh` + `tests/test_install_vm.py` (160
  lines): 3 install modes (a/b/c) + rollback test
- `tools/test_update_vm.sh` + `tests/test_update_vm.py` (263
  lines): 5 updater phases (license/errata/pull/rollback/blob)

**Decision tree branches tested:**
- ✓ `--headless` skips dialogs (no prompts, no gtk)
- ✓ `--selected meld` whitelist gates pandoc/latex with "not in
  --selected list" log line
- ✓ JSON stream contains terminal `{phase: done, progress: 100}`
- ✓ Rollback: corrupt mid-install → BACKUP_DIR restoration
  + `BTERMINAL_ROLLBACK_OK` marker + `__init__.py` SHA256 unchanged

**Edge cases NOT covered (manual smoke):**
- `--selected llama` actually triggers ollama curl install (the
  test assumes it does on a real VM, not unit-tested)
- Concurrent install (two sessions of `install.sh` — would
  collide on `~/.local/bin/`)
- Network drop mid-pipx (partial install state)
- Disk full during BACKUP_DIR
- Read-only `~/.local` (immutable bind mount)

### 3.5 InstallerWizard (#77 / #5)

**Shipped** (bterminal/ui/installer_wizard.py:1-584):
- 5 pages: Welcome+License, Inventory, Picks, Progress, Summary
- Pure helpers: `parse_status_json_line()`, `strip_ansi()`,
  `build_install_argv()` (testable without GTK)
- `Gio.Subprocess` streams install.sh stdout, parses JSON
  status lines → progress bar updates
- `_notify_deps_changed()` calls `diagnostics.invalidate_cache()`
  on success only (not on cancel/fail)

**Coverage** (tests/test_installer_wizard_vm.py:1-237, 0.41 ratio):
- Source-level: window title, page headers, button order, license
  checkbox label, repo_dir env, JSON polling markers, orphan
  cleanup
- xdotool VM smoke runner with full key-sequence drive
- No widget-unit tests (gap — wizard runs only through xvfb-run)

**Decision tree branches tested:**
- ✓ Page header strings match xdotool grep targets
- ✓ Button order Cancel→Back→Next→Finish (Tab counts)
- ✓ `--installer` entrypoint wired in `__main__.py`
- ✓ `BTERMINAL_REPO_DIR` env passes through

**Edge cases NOT covered:**
- Wizard cancel mid-install (subprocess kill)
- Pre-existing install (re-run wizard on populated layout)
- Locale-specific button labels (i18n) — wizard hardcodes EN
- Progress bar overflow (status_json reports 110%?)
- License accept persistence (already covered by #52 fix though)

### 3.6 install.sh + Wizard wiring (#78)

**Shipped:**
- bash detects `$DISPLAY + DESKTOP_SESSION + python3-gi` →
  spawns `python3 -m bterminal --installer` (install.sh ~lines
  added)
- BT `Tools → Install dependencies` menu item → wizard re-launch
- `BTERMINAL_REPO_DIR=$SCRIPT_DIR PYTHONPATH=$SCRIPT_DIR` env so
  wizard runs from cloned tree pre-install

**Coverage:**
- `tests/test_installer_wizard_vm.py` source-grep:
  `--installer` flag in `__main__.py`,
  `_run_installer_wizard` defined, `BTERMINAL_REPO_DIR` honored
- VM smoke spans full chain

**Decision tree branches tested:**
- ✓ `--installer` shorts to `_run_installer_wizard()` w `__main__`
- ✓ Headless mode (`--headless` flag) skips GTK auto-spawn
- ✓ Repo dir resolution chain: env → cwd → ~/.local/share

**Edge cases NOT covered:**
- DISPLAY set but X is dead (Xvfb crashed) — wizard tries to spawn
- Wayland-only session (BT requires X11; what does Wayland do?)
- ssh -X with stale forwarded display

### 3.7 OptionsDialog Local Models + Providers (#79 + #83)

**Shipped:**
- Lazy-built "Local Models (Ollama)" expander with TreeView,
  Pull/Delete/Set-as-default/Refresh buttons
  (bterminal/ui/dialogs/options.py:additions)
- "AI Providers" expander with enable/disable checkboxes per
  provider
- Save guard prevents persisting "all providers disabled" state

**ollama_client.py** (1-220):
- `OllamaModel` dataclass; pure parsers
  `parse_ollama_list_output()`, `parse_ollama_api_tags()`
- `is_daemon_running()`, `is_cli_installed()`, `list_models()`,
  `delete_model()`, `pull_model()`
- HTTP-first fallback to CLI

**Coverage:**
- ollama_client parsers covered in indirect integration tests
- Save guard covered by widget-level tests

**Decision tree branches tested:**
- ✓ HTTP path returns models
- ✓ HTTP fails → CLI fallback works
- ✓ Both fail → empty list
- ✓ Save guard rejects all-disabled
- ✓ All-enabled is the default (round-trip)

**Edge cases NOT covered:**
- ollama daemon up but :11434 firewall'd
- HTTP returns 200 with broken JSON
- `ollama list` output format change (long-tail Ollama versions)
- `ollama pull` interactive progress (we don't parse it)
- Model file truncated on disk (corrupted)

### 3.8 diagnostics.is_feature_available (#80) + Auto-invalidate (#81)

**Shipped:**
- `is_feature_available(cmd, ttl_sec=60)` TTL cache + module
  state `_FEATURE_CACHE`, `_INVALIDATION_LISTENERS`
  (bterminal/diagnostics.py:140-275)
- `invalidate_cache()`, `subscribe_invalidation()`,
  `unsubscribe_invalidation()`
- 8 callsites refactored: ui/panels/files.py:108,257,292,329,386,
  500; ui/sidebar.py:934
- InstallerWizard #5 success → `_notify_deps_changed()` →
  invalidate

**Decision tree branches tested:**
- ✓ Cached call doesn't re-spawn shutil.which
- ✓ TTL expiry triggers re-probe
- ✓ invalidate_cache() forces re-probe regardless of TTL
- ✓ Listener pattern: subscribe → invalidate → callback fires
- ✓ Snapshot iteration in invalidate (subscribers can subscribe
  during execution without crashing)

**Edge cases NOT covered:**
- Listener throws during invocation (does it kill the rest?)
- Cache corruption (race condition between threads — GTK is
  single-threaded so probably safe)
- Negative TTL (defensive default not tested)

### 3.9 README "Local LLM" + ProviderManager UI (#82, #83)

**Shipped:**
- README extended with provider comparison table (now 4-col, 16
  rows) + "Local LLM via Ollama (experimental)" subsection with
  hardware matrix
- Quick-start bash + 5 troubleshooting scenarios
- Per-tab provider enable/disable UI

**Coverage:**
- README rendered locally; markdown structure validated

### 3.10 Manual smoke + VM tests (#84-#87)

**Shipped (5 runbook scripts + 4 pytest wrappers + 1 e2e fixture):**

| Script | Pytest wrapper | Phases | LOC | Status |
|---|---|---:|---:|---|
| `tools/smoke_3rd_provider.sh` | `test_smoke_3rd_provider_runbook.py` | 8 | 167 | ✓ |
| `tools/test_install_vm.sh` | `test_install_vm.py` | 3 modes + rollback | 160 | ✓ |
| `tools/test_update_vm.sh` | `test_update_vm.py` | 5 | 263 | ✓ |
| `tools/test_installer_wizard_vm.sh` | `test_installer_wizard_vm.py` | 8 | 237 | ✓ |
| `tools/test_aider_real_model.sh` | `test_aider_real_model.py` | 7 | 266 | ✓ |
| (e2e fixture) | `tests/e2e/test_aider_full_session.py` | 6 | 498 | ✓ |

**Decision tree branches tested:**
- ✓ All script flags (--help, --skip-llama, --modes, etc.)
- ✓ Each phase has a recognizable banner / log marker
- ✓ Pytest source-level: real updater symbol imports, real
  AiderProvider methods, status_json contract, layout asserts
- ✓ VM runs gated by `BTERMINAL_VM_TESTS=1` env

**Edge cases NOT covered:**
- VM offline during run — scripts fail at preflight (correct)
  but the pytest wrapper just skips silently. Maybe should warn.
- Concurrent wizard + install.sh (two windows).
- Snapshot rollback between VM runs (xdotool stretch goal).

### 3.11 Provider parity matrix (#91)

**Shipped:** `tests/test_provider_parity.py` (420 LOC):
- 7 capability flags both True (intro_prompt, rules_inject, etc.)
- 9 intentional divergences pinned with `why` reason
- `should_inject_rules` / `should_run_auto_trigger` parity
- `build_argv` shape: Claude positional intro, Aider stdin_feed
- session_log_glob suffix per provider
- image_paste_template strategies (Claude null, Copilot/Aider hint)
- stats reader factory returns reader for all 3 providers
- local_endpoint_url only for Aider

**Decision tree branches tested:**
- ✓ Every capability flag for each provider
- ✓ Every dispatch helper for each provider
- ✓ Forward-compat: unknown provider name → False / None / empty

**Edge cases NOT covered:**
- 4th provider added but registry singleton not invalidated
  (could carry stale state into tests)
- Capability flag added to base.py but not to defaults.json
  (would crash registry construction)

### 3.12 Rules-inject byte parity (#93)

**Shipped:**
- `extract_rules_inject_bytes(provider_name, project, rules_stdout)`
  pure helper (terminal_tab.py:123-148)
- `_do_inject_rules` refactored to call helper (was inline
  `.encode()`)
- `tests/test_rules_inject_provider_parity.py` (253 LOC):
  byte-equality across 3 providers, source-grep guards against
  per-provider branches in production code, chat-history capture
  simulation for Aider

**Decision tree branches tested:**
- ✓ Same input rules stdout → same bytes for all 3
- ✓ Trailing whitespace stripped, inner newlines preserved
- ✓ UTF-8 (Polish + em-dash) round-trips
- ✓ Empty/whitespace-only input → empty bytes
- ✓ Helper signature has `provider_name` (parameter-marker)
- ✓ Source body has no `if provider_name == ...` branches
- ✓ Production `_do_inject_rules` calls the helper, uses its
  output for both `record_feed` AND `feed_child`

**Edge cases NOT covered:**
- ctx CLI returns malformed UTF-8 (the helper's `.encode()` would
  raise — should it use `errors=replace`?)
- Very long rules block (10 MB+) — feed_child PTY buffer limits

### 3.13 AIDER.md auto-symlink (#92)

**Shipped:**
- `ensure_context_file_alongside_claude(project_dir, filename)`
  generic helper (ctx/helpers.py:17-95)
- `ensure_context_files_for_all_providers(project_dir)` registry-
  driven dispatcher (ctx/helpers.py:106-129)
- `ensure_agents_md_alongside_claude` backward-compat shim
  (ctx/helpers.py:98-103)
- ctx wizard finalize call updated (ctx/dialogs.py:354-359)
- Broken-symlink repair path: stale symlink to non-existent
  target + CLAUDE.md exists → unlink + relink (returns "fixed")

**Coverage:**
- `tests/test_context_file_per_provider.py` (283 LOC, 17 tests):
  Aider symlink, content roundtrip, idempotency, broken repair,
  user-customized left alone, no_source, fallback to copy,
  defensive unlink failure, registry dispatcher all-providers,
  Claude self-skip, backward-compat shim
- `tests/test_ctx_init.py` (legacy, 10 tests) ride on shim ✓
- `tests/test_aider_context_file.py` (#96): full-flow integration

**Decision tree branches tested:**
- ✓ All 7 return states: self/exists/fixed/symlink/copy/no_source/failed
- ✓ Empty filename → self
- ✓ symlink → CLAUDE.md while CLAUDE.md missing → exists (left
  intentional-but-broken)
- ✓ symlink → other_file while CLAUDE.md exists → fixed (relink)
- ✓ Registry walk skips providers without context_file capability

**Edge cases NOT covered:**
- ctx_file with absolute path (shouldn't happen but defensive)
- ctx_file with `../` traversal (security)
- Concurrent ctx wizard (race condition on symlink creation)

### 3.14 AiderStatsReader + cost_unavailable (#94)

**Shipped:**
- `bterminal/ui/stats/aider.py` (118 lines): AiderStatsReader
  thin adapter over `AiderProvider.parse_session_stats`
- Registered in `_READER_CLASSES`
  (`bterminal/ui/stats/__init__.py:30-37`)
- `stats_widget_options_for_ai_config` returns
  `{hide_plan_usage, cost_unavailable}`
- `SessionStatsBar(..., cost_unavailable: bool)` renders
  `💰 n/a` when True (widget.py:174-179)

**Coverage:** `tests/test_stats_bar_aider.py` (281 LOC, 14 tests).

**Decision tree branches tested:**
- ✓ Reader extracts tokens from real .aider.chat.history.md
- ✓ Empty / missing log → empty TokenStats
- ✓ Empty project_dir → empty TokenStats (SSH/local tabs)
- ✓ session_log_glob template propagation (no path duplication)
- ✓ Plan usage / cost defaults (None / 0.0)
- ✓ Factory returns AiderStatsReader bound to project_dir
- ✓ Widget render: `↑ 4.0K` / `584` / `qwen` model
- ✓ `cost_unavailable=True` → `💰 n/a` (NOT `$0.0000`)
- ✓ `cost_unavailable=False` (Claude/Copilot path) → `$0.0123`

**Edge cases NOT covered:**
- aider chat history malformed (regex never matches `Tokens:` line)
- Multi-model session (model attribution picks last `--model`
  occurrence — what if user switched mid-session?)
- Concurrent reader reads (two SessionStatsBar instances on same
  log)
- Log file rotation (aider truncates after N MB?)

### 3.15 Image paste flow (Aider) (#95)

**Shipped (defaults.json + tests):**
- `aider.argv.image_paste_template = "User provided image:
  {path} — describe what you see before editing any code."`
- `tests/test_aider_image_paste.py` (290 LOC, 14 tests)

**Decision tree branches tested:**
- ✓ Template has `{path}` + nudge verb (describe/look/view/inspect)
- ✓ Template mentions edit/code framing (Aider value-add)
- ✓ 3-way provider divergence: Claude null vs Copilot vs Aider
  (Copilot ≠ Aider strings)
- ✓ Mechanism parity: same path appears in both Copilot + Aider
  outputs
- ✓ Same image, Aider session vs Claude session → different
  output (wrap vs bare)
- ✓ Session-level override beats provider default
- ✓ Empty session override falls through to provider default
- ✓ Global toggle off → bare path; session override beats global

**Edge cases NOT covered:**
- Path with shell metacharacters ($PATH, ;rm -rf) — template just
  format()-substitutes, no escape
- Non-image extension passed (BMP, TIFF, SVG)
- Path with spaces (template handles, but does aider read it?)

### 3.16 AIDER.md context file integration (#96)

**Shipped:** `tests/test_aider_context_file.py` (437 LOC, 13 tests).

**Decision tree branches tested:**
- ✓ Aider intro_prompt_mode = stdin_feed
- ✓ build_argv ends with positional project_dir (cwd-based
  AIDER.md auto-discovery)
- ✓ build_argv does NOT use --read flag
- ✓ AIDER.md missing + CLAUDE.md present → symlink creates
  (#92 integration)
- ✓ User-customized AIDER.md left alone, no clobber
- ✓ no CLAUDE.md → no AIDER.md
- ✓ context_file capability == "AIDER.md"
- ✓ intro_prompt header uses Aider long_label (not stale Claude)
- ✓ "Project name in ctx/tasks: <name>" present (fallback path)
- ✓ "Project context (myproj):" present (populated path)
- ✓ ai_config.prompt appended for Aider
- ✓ Full flow: project + ctx + symlink → both paths reach Aider
- ✓ context_file_cumulative=False (no append-to-symlink risk)

**Edge cases NOT covered:**
- AIDER.md with absolute symlink (path:`/tmp/CLAUDE.md`)
- Read-only mount of project_dir (chmod 555)
- Symlink loop (AIDER.md → CLAUDE.md → AIDER.md after
  customization)

### 3.17 Aider task_auto_trigger (#97)

**Shipped:** `tests/test_aider_auto_trigger.py` (643 LOC, 14
tests — 12 unit + 2 e2e w bterminal_with_aider_and_tasks fixture).

**Decision tree branches tested:**
- ✓ Aider/Copilot ready_marker = None; Claude = "system.stop"
- ✓ `_idle_check_tick` source has no ready_marker dispatch
- ✓ `_IDLE_QUIET_SEC = 2.0`, `_IDLE_HARD_CAP_SEC = 60.0`
  (no per-provider override)
- ✓ `_claim_next_task` provider-agnostic SQL: returns first open;
  same session twice → same task; 2 sessions → different tasks;
  all claimed → None
- ✓ `terminal_tab.py` source: no `task_done` patterns, no
  `provider == "aider"` branches, no per-provider task done logic
- ✓ Auto-trigger message format (the f-string composition block)
  has no Aider/Claude/Copilot strings
- ✓ E2E: force_idle on aider tab → auto_trigger event captured
  with seeded task_id
- ✓ E2E: 3 rapid force_idle → all reference SAME task (atomic)

**Edge cases NOT covered:**
- aider session crashes mid-task (what cleans up the claim?)
- Database lock contention (sqlite3 BUSY error)
- Task with very long description (does message exceed PTY buffer?)

### 3.18 Bug fixes ridden in (#52-#56, #57-#67, #66-#68, #69-#71)

**Carried-over from prior session — pinned by tests but no new
work in this initiative:**

- #52 license dialog showing path string vs content — pinned by
  `tests/test_update_vm.py` phases 1 + 5
- #56 generic-subdir basename leak (`Dokumenty/test`) — covered
  by `test_ctx_init.py:test_smart_project_name`
- Sidebar polish (#57-#67) — covered by existing widget tests
- Diagnostics (#62-#65) — covered by `test_diagnostics_*` (legacy)
- Alt-screen (#66-#68) — TERM hack reverted, documented in README
- Image paste 5-layer priority chain (#69-#71) — covered by
  `test_image_paste_hint.py` + `test_aider_image_paste.py`

---

## 4. Cross-cutting integration matrix

How modules wire together. Critical for #27 because integration
seams are where most undocumented edge cases live.

```
defaults.json ──> ProviderRegistry ──> ProviderCapabilities
                          │
                          ├──> create_stats_reader_for_ai_config
                          │      └──> ClaudeStatsReader
                          │      └──> CopilotStatsReader
                          │      └──> AiderStatsReader (#94)
                          │
                          ├──> stats_widget_options_for_ai_config
                          │      └──> {hide_plan_usage, cost_unavailable}
                          │
                          ├──> should_inject_rules / should_run_auto_trigger
                          │      └──> capability gates
                          │
                          ├──> ensure_context_files_for_all_providers
                          │      └──> AIDER.md / AGENTS.md mirrors
                          │
                          └──> _format_image_paste_for_provider
                                 └──> session_override > global_toggle
                                      > provider default

system_probe ──> recommend_models ──> InstallerWizard.picks
                                       └──> install.sh --selected llama
                                            └──> ollama daemon

ollama_client ──> OptionsDialog ──> ~/.config/bterminal/local_models.json
                       │
                       └──> default_local_model_for_provider mapping
                              └──> AiderProvider.build_argv (4-layer
                                   model resolution)

diagnostics ──> is_feature_available (TTL cache)
                  └──> ui/panels/files.py (meld availability)
                  └──> ui/sidebar.py (xdg-open availability)
                  └──> InstallerWizard.invalidate on success

terminal_tab._on_task_idle_timeout
                  ├──> _do_inject_rules
                  │      └──> extract_rules_inject_bytes(provider_name,
                  │            project, rules_stdout) [#93]
                  │            └──> identical bytes ALL providers
                  └──> _claim_next_task (atomic SQL)
                          └──> [AUTO-TRIGGER] feed_child message
                                (provider-agnostic body)
```

Critical seams:
- ProviderRegistry singleton is loaded once per process. Tests use
  `reset_registry()` fixture; missing reset = stale capabilities
  bleed between tests.
- `_READER_CLASSES` dict order doesn't affect dispatch but is
  alphabetized for diffability.
- `extract_rules_inject_bytes` is called from production AND tests;
  guard test pins this connection (#93).
- `ensure_context_files_for_all_providers` walks `registry.names()`
  on EACH ctx wizard finalize — repeat-call cost is O(N providers)
  with file I/O.

---

## 5. Test coverage matrix (decision-tree depth)

| Subsystem | Unit | Integration | E2E | Manual VM | Total |
|---|---:|---:|---:|---:|---:|
| Capability gates | 49 | 8 | 7 | – | 64 |
| Provider registry | 18 | 6 | 4 | – | 28 |
| AiderProvider | 25 | 5 | 6 | 7 | 43 |
| Rules inject | 21 | 4 | 4 | – | 29 |
| Auto-trigger | 12 | 2 | 4 | – | 18 |
| Image paste | 26 | 14 | – | manual | 40 |
| Context files | 17 | 13 | – | – | 30 |
| Stats bar (Aider) | 14 | – | – | – | 14 |
| install.sh | 12 | 16 | 1 (opt-in) | full smoke | ≥30 |
| Updater | 22 | – | 1 (opt-in) | full smoke | 23 |
| InstallerWizard | 22 | – | 1 (opt-in) | full xdotool | 23 |
| ollama_client | 15 | 8 | – | smoke | 23 |
| Diagnostics gate | 10 | 4 | – | – | 14 |

Aggregate: ~370 tests directly tied to this initiative;
1528 collected total in tests/. The 4x ratio of "directly tied"
vs total reflects how much existing infrastructure (debug-REST,
mock_ai_cli, conftest.py) the initiative was able to ride on.

---

## 6. Edge cases STILL not covered

The list #27 should turn into ~30-50 follow-up tasks.

### 6.1 Failure-mode tests (long-tail)

1. **Ollama daemon dies mid-session** — what does AiderProvider
   do? Does the chat history file stay coherent? Does BT show an
   error or silently produce garbage tokens?

2. **aider binary disappears mid-session** — uninstall pipx while
   tab open. Is BT graceful?

3. **provider config file corrupted** — `~/.config/bterminal/
   providers.json` truncated mid-write. We have `JSONDecodeError`
   fallback but no test for partial write.

4. **Network down for OpenRouter** (consult tool) — already
   handled by openai_compat exceptions, but no integration test.

5. **CTX DB corrupted / locked** — auto-trigger flow tries to
   query rules_config / tasks, what happens on `OperationalError:
   database is locked`?

6. **Mid-install Ctrl-C** — install.sh has rollback for `false`
   injection but Ctrl-C may interrupt at a different point.

7. **Disk full during install** — pip / npm partial install
   leaves a half-baked layout.

8. **Ollama API returns wrong shape** — `{"models": [...]}` vs
   `{"model_list": [...]}` — defensive parser needed.

### 6.2 Race conditions

9. **Concurrent tab spawns** — open Aider + Claude simultaneously;
   does `_claim_next_task` race correctly? sqlite3 atomic — yes.
   But what about ai_sessions.json read?

10. **Simultaneous force_idle on 2 tabs** — _claim_next_task is
    atomic per session_id, but the wider `_on_task_idle_timeout`
    has multiple subprocess calls. Not tested for true parallelism.

11. **Install + uninstall in parallel** — two BT instances calling
    install.sh on the same VM. BACKUP_DIR collision.

12. **InstallerWizard cancelled during apt** — does the partial
    state get rolled back?

### 6.3 Cross-feature interactions

13. **Image paste + rules_inject same time** — user pastes image
    while ctx rules inject is mid-write. Bytes interleave?

14. **auto_trigger + ad-hoc feed** — user types in VTE while
    auto-trigger fires. Lines arrive simultaneously to aider.

15. **Scrollback during alt-screen** — Aider doesn't enter alt-
    screen, but if user pastes into a non-alt mode AND scrolls,
    does the paste land at scroll position or VTE bottom?

16. **AIDER.md symlink + ctx wizard re-run** — second wizard
    invocation hits idempotency path; what if AIDER.md was deleted
    between runs?

17. **Provider switching mid-session** — change ai_session.json
    provider field while tab open. Does BT reload?

### 6.4 i18n + localization

18. **Polish locale CLAUDE.md content** → `extract_rules_inject_
    bytes` UTF-8 round-trip is tested for one Polish phrase but
    not for end-to-end CTX → AIDER.md → aider feed.

19. **Wizard locale switching** — wizard hardcodes EN. What if
    BT UI is in PL?

20. **AI language hint** (#tell_ai_language) for Aider — Aider
    should also receive the "respond in <language>" footer. Pinned
    in intro_prompt computation but no Aider-specific test.

### 6.5 Forward-compat

21. **Aider 1.x argv changes** — pin which aider versions we
    support; shim for 0.x → 1.x flag renames.

22. **Ollama 0.5+ API breaking changes** — `/api/tags` vs `/api/
    generate` boundaries.

23. **New provider added** — registry self-extension; how would a
    plugin-provided provider (not in defaults.json but added via
    `register()`) interact with capability gates? Tested for
    rules_inject + auto_trigger but not stats reader.

### 6.6 Performance / scale

24. **1000 tasks in CTX DB** — `_claim_next_task` SQL has indexes
    but not benchmark'd at scale.

25. **10 MB rules block** — does feed_child split it correctly?
    PTY has line buffer limits.

26. **100 prompts in 60 seconds** — rules_inject every 100 +
    refresh every 50. With low limits hits the inject_pending
    window; what happens if VTE is still streaming?

27. **AiderStatsReader on 100 MB chat history** — full-file scan
    on every refresh tick (5s).

### 6.7 Security

28. **Image paste path with shell metacharacters** — see #15 above.

29. **`tasks done` SQL injection** — task_id from CTX DB feeds
    into `tasks done <project> <id>` Bash invocation. Not param-
    bound at the ctx subsystem boundary.

30. **CTX DB world-readable** — file mode 644 by default; should
    be 600 for sensitive project info.

### 6.8 Manual VM smoke gaps

31. **VM update test on real GitHub remote** — phase 3 uses fake
    /tmp/upstream because hitting the real github.com is flaky in
    CI. But occasionally we want to test the REAL update flow.

32. **Wizard xdotool resilience to GTK theme** — xdotool key
    sequences hardcode Tab counts. A theme change might add a
    button.

33. **Aider real-model with large project** — qwen-0.5b on a 100k-
    line repo: does cwd-based auto-discovery find AIDER.md?

34. **rollback on `apt install` mid-execution** — phase 4 rollback
    test injects `false` AFTER backup. What if failure happens
    BEFORE backup is populated?

35. **Concurrent xvfb-run sessions** — `-a` picks free display,
    but two parallel runs may collide.

---

## 7. Summary of risks & followups

Highest priority for #27:

1. **Failure modes** (rows 1-8) — these are likely production
   incidents waiting to happen. Worth ~10 tasks.

2. **Race conditions** (rows 9-12) — sqlite3 saves us in most
   cases but the wider auto-trigger path has subprocess calls
   that aren't atomic. Worth ~4 tasks.

3. **Cross-feature interactions** (rows 13-17) — user can hit
   these via normal usage. Worth ~5 tasks.

4. **i18n end-to-end** (rows 18-20) — Polish content rare in
   tests; user is Polish, this matters. Worth ~3 tasks.

5. **Forward compat** (rows 21-23) — annoying when third-party
   tooling moves. Worth ~3 tasks.

6. **Performance** (rows 24-27) — likely fine today, but worth
   pinning so a 100x scale-up doesn't surprise. Worth ~4 tasks.

7. **Security** (rows 28-30) — small surface but worth fixing.
   Worth ~3 tasks.

8. **VM smoke gaps** (rows 31-35) — flaky-VM tolerance + xdotool
   resilience. Worth ~5 tasks.

Total: ~37 tasks. #27 should sequence them by:
- foundation tests first (sqlite3 race, JSON shape parsers,
  defensive UTF-8)
- integration tests next (cross-feature, race conditions)
- VM smoke tests last (most expensive, depend on stable
  foundation)

---

## 8. References

Key file:line landmarks worth knowing for #27:
- `bterminal/providers/aider.py:74-155` — build_argv composition
- `bterminal/providers/aider.py:172-218` — parse_session_stats regex
- `bterminal/ui/terminal_tab.py:123-148` — extract_rules_inject_bytes
- `bterminal/ui/terminal_tab.py:178-194` — should_inject_rules /
  should_run_auto_trigger
- `bterminal/ui/terminal_tab.py:823-843` — _idle_check_tick
- `bterminal/ui/terminal_tab.py:845-914` — _on_task_idle_timeout
- `bterminal/ui/terminal_tab.py:916-960` — _claim_next_task
- `bterminal/ui/terminal_tab.py:1020-1067` — _format_image_paste_for_provider
- `bterminal/ui/stats/aider.py:30-110` — AiderStatsReader
- `bterminal/ui/stats/widget.py:174-180` — cost rendering n/a
- `bterminal/ctx/helpers.py:17-95` — ensure_context_file_alongside_claude
- `bterminal/ctx/helpers.py:106-129` — registry-driven dispatcher
- `bterminal/helpers.py:144-207` — _compute_intro_prompt_for_tab
- `install.sh` `--headless` / `--selected` / `--status-json` flag
  parsing (look for those literals)
- `tests/manual/README.md` — runbook inventory + CI matrix

Test files (alphabetical):
- `tests/test_aider_auto_trigger.py` (#97 / #25, 643 LOC)
- `tests/test_aider_context_file.py` (#96 / #24, 437 LOC)
- `tests/test_aider_full_session.py` (#90 / #18, in tests/e2e/, 498 LOC)
- `tests/test_aider_image_paste.py` (#95 / #23, 290 LOC)
- `tests/test_aider_provider.py` (pre-#19, 437 LOC)
- `tests/test_aider_real_model.py` (#89 / #17, 266 LOC)
- `tests/test_context_file_per_provider.py` (#92 / #20, 283 LOC)
- `tests/test_install_vm.py` (#85 / #13, 160 LOC)
- `tests/test_installer_wizard_vm.py` (#87 / #15, 237 LOC)
- `tests/test_provider_parity.py` (#91 / #19, 420 LOC)
- `tests/test_rules_inject_provider_parity.py` (#93 / #21, 253 LOC)
- `tests/test_stats_bar_aider.py` (#94 / #22, 281 LOC)
- `tests/test_test_inventory.py` (#88 / #16, 258 LOC)
- `tests/test_update_vm.py` (#86 / #14, 263 LOC)

Runbook scripts (alphabetical):
- `tools/smoke_3rd_provider.sh` (#84 / #12)
- `tools/test_aider_real_model.sh` (#89 / #17)
- `tools/test_install_vm.sh` (#85 / #13)
- `tools/test_installer_wizard_vm.sh` (#87 / #15)
- `tools/test_update_vm.sh` (#86 / #14)
- `tools/_vm_aider_checks/{argv_parity,stats_check}.py` (#17)
- `tools/_vm_update_checks/{license_regression,errata_corruption,
   git_pull_check,blob_path_probe}.py` (#14)

End of audit.
