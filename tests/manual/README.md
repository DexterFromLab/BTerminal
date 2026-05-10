# BTerminal — Manual VM Test Runbooks

Tests in this directory are **not run by the default `pytest`** invocation
because they need a real VM, a real GTK display, or both. They're
meant for release verification and for catching regressions that live
between unit-tested layers.

## Pre-requisites

- A Linux VM reachable over SSH. Default alias: `vm-test` (configure
  in `~/.ssh/config`). Override with `VM_HOST` env var.
- VM has installed: `xvfb-run`, `python3 ≥ 3.10`, `npm ≥ 10`, `sudo`
  (unless you pass `--no-sudo` to scripts), `git`, `gir1.2-gtk-3.0`,
  `gir1.2-vte-2.91`.
- Run all scripts from the BTerminal repo root on the host.

---

## Inventory

| Script | What it covers | Runtime |
|---|---|---|
| `tools/smoke_3rd_provider.sh` | Aider + Ollama 3rd-provider stack end-to-end (#84) | ~5–10 min |
| `tools/test_install_vm.sh` | install.sh in 3 modes + rollback (#85) | ~3 min |
| `tools/test_update_vm.sh` | BT updater flow — license / errata / pull / rollback (#86) | ~5 min |
| `tools/test_installer_wizard_vm.sh` | GTK wizard via xdotool key sequences (#87) | ~10 min |
| `tools/test_aider_real_model.sh` | Real Ollama + Qwen-0.5B prompt/response (#89) | ~3 min |
| `tests/e2e/test_aider_full_session.py` | Aider session in BT, full workflow (#90) — runs under default `pytest tests/e2e/` | ~10 s |

---

## Slow / e2e markers — full audit (task #88)

This section is the canonical answer to "where did my slow tests go?"
The short answer: they are still collected by default. `pytest.ini`
registers the `slow` marker but does NOT add a global `addopts =
-m "not slow"`, so a bare `pytest tests/` runs every test file —
including the three slow ones below.

### Pytest run modes

| Command | Collects | When to use |
|---|---|---|
| `pytest tests/` | everything (~1362 tests, 3 slow + 68 e2e) | local pre-commit, CI nightly |
| `pytest -m slow` | only @pytest.mark.slow (3 tests) | when wall-clock-bound logic changes |
| `pytest -m "not slow"` | skip slow (1359 collected) | local fast loop, CI on every push |
| `pytest tests/e2e/` | full e2e layer (68 tests) | after cross-cutting refactor |
| `pytest tests/e2e/ -m "not slow"` | e2e minus slow | quick e2e smoke (~30 s) |
| `./tools/test_all.sh --slow-only` | the 3 slow tests, verbose | manual sanity-check |
| `./tools/test_all.sh --e2e` | tests/e2e/ minus slow, verbose | targeted e2e re-run |

### `@pytest.mark.slow` inventory (exactly 3 tests)

| Test | What it does | Why slow |
|---|---|---|
| `tests/test_exploration.py::test_exploration` | random-walk explorer issues 1000 REST commands against a live BT subprocess | full `bterminal_process` fixture spin-up + 1000 round-trips → ~10 s |
| `tests/test_manifests.py::test_btmsg_starts_and_health` | spawns `btmsg` plugin daemon (Flask, real free port), polls `/api/health` until live | bind to free port + plugin daemon import cost → ~3 s |
| `tests/test_idle_timeout.py::test_idle_watchdog_stops_server` | wall-clock idle timer — 2-second sleep, watchdog must reap | unavoidable real-time wait → ~2 s |

Source-pinned by `tests/test_test_inventory.py` so a 4th @slow somewhere
without updating this table fails CI on the host.

### `tests/e2e/` inventory (11 test files + README + `__init__.py`)

All e2e files use the `bterminal_process` fixture from `tests/conftest.py`
(spawns BT subprocess under `xvfb-run`, with debug-REST + mock CLI
binaries on PATH). They aren't `slow`-marked, but they are heavy
(seconds-per-test, not milliseconds).

| File | What it covers |
|---|---|
| `test_cli_tools_smoke.py` | ctx / tasks / consult / memory_wizard launchable in BT subshell |
| `test_dual_provider_workflow.py` | claude + copilot (now + aider) tab parity flow |
| `test_feed_capture_foundation.py` | feed_log debug surface — provider events captured |
| `test_intro_prompt_structure.py` | per-provider intro prompt body shape (CLAUDE.md / AGENTS.md / AIDER.md) |
| `test_per_tab_plugin_gating.py` | manifests gating per provider tab |
| `test_provider_switching.py` | switching providers mid-session via REST `/api/tabs/ai/{provider}` |
| `test_sidebar_context_menu_rest.py` | sidebar right-click → action dispatch via REST |
| `test_smoke_battery.py` | combined smoke battery — all REST endpoints respond |
| `test_tier1_acceptance.py` | tier 1 (must-pass) acceptance for new tab open + intro |
| `test_tier2_acceptance.py` | tier 2 (parity) acceptance — provider switching, session log |
| `test_tier3_acceptance.py` | tier 3 (stretch) — image paste + auto-trigger flow |

### CI matrix recommendation

| Branch | Mode | Why |
|---|---|---|
| `feature/*`, PR | `pytest -m "not slow"` (~25 s) | fast feedback, slow tests run nightly |
| `master` push | `pytest tests/` (full ~1 min) | catch slow regressions before release |
| `release/*` | `pytest tests/` + `tools/test_all.sh --e2e` + manual VM scripts | full pre-release gate |
| nightly cron | `tools/test_all.sh --slow-only` + e2e | long-tail regression net |

The current GitHub Actions workflow (if/when added) should pin to the
`pytest -m "not slow"` mode for PR-time + add a separate scheduled job
for the slow + manual VM scripts. VM-bound tests (#85/#86/#87) require
a self-hosted runner with the `vm-test` SSH alias configured.

### `bterminal_process` fixture quick reference

The fixture in `tests/conftest.py` does roughly:
1. Spawns `xvfb-run python3 -m bterminal --debug-rest` with isolated
   `~/.config/bterminal` + `~/.local/share/bterminal` tmp dirs.
2. Reads the rest token from BT's stdout, exposes it on the fixture.
3. Polls `/api/health` until 200 (or 401 — auth-wall acceptable).
4. Yields a `Process` namedtuple `(rest_url, token, pid, log_path)`.
5. Teardown: SIGTERM, drain stdout, assert exit code in {0, -SIGTERM}.

Fixture scope is `function` — every test gets a fresh BT subprocess.
That's why e2e is heavy. Don't add session-scoped state: parallel
e2e via `pytest -n` would race on debug-REST tokens.

---

## `tools/smoke_3rd_provider.sh` — full runbook

### Run

```bash
./tools/smoke_3rd_provider.sh
# Skip the curl|sh ollama install:
./tools/smoke_3rd_provider.sh --skip-llama
# Keep VM state from a prior run (don't wipe ~/.local/share/bterminal):
./tools/smoke_3rd_provider.sh --no-wipe
```

### Phases

| # | Step | Pass criteria |
|---|---|---|
| 0 | Preflight | `ssh vm-test echo OK` succeeds + xvfb-run/python3/npm present |
| 1 | Wipe BT state | `~/.local/share/bterminal` + `~/.config/bterminal` removed |
| 2 | Sync source | `tools/vm_sync.sh` exits 0; rsync exit code captured in `smoke-logs/vm_sync.log` |
| 3 | install.sh --headless | exit 0; stdout JSON stream contains terminal `{phase: done, progress: 100}` |
| 4 | Layout verification | `bterminal/__init__.py` + `~/.local/bin/bterminal` symlink + `defaults/icons/aider.svg` exist |
| 5 | Ollama bring-up | `ollama --version` ok; `:11434/api/tags` 200; `qwen2.5-coder:0.5b` in `ollama list` |
| 6 | BT spawn + Aider tab | xvfb-run BT with `--debug-rest`; `/api/health` 200 (or 401 for auth wall); `POST /api/tabs/ai/aider` returns ok=true; `/api/tabs` shows entry with `provider=aider` |
| 7 | Image paste template | `defaults.json:providers.aider.argv.image_paste_template` non-empty + contains `{path}` |

### Output

- Per-phase PASS/FAIL printed live with green ✓ / red ✗.
- Full logs collected into `./smoke-logs/<phase>.log` (gitignored).
- Final exit code: 0 only when ALL phases pass.

### Common failure modes

- **Phase 3 fail**: `install.sh` doesn't exit 0. Check `smoke-logs/install.log` — usually `--no-sudo` blocks an `auto`-tier dep that the VM lacks. Manually `sudo apt install <missing>` once, retry.
- **Phase 5 fail (ollama-daemon)**: VM blocks systemctl --user (no D-Bus session). Workaround: SSH to VM and start manually `nohup ollama serve &` then re-run with `--no-wipe`.
- **Phase 6 fail (bt-spawn)**: license seed mismatch — check `tests/_subprocess_helpers.py:seed_license` is current on VM (reflects post-#52 license schema).
- **Phase 6 fail (open-aider-tab)**: aider binary not on $PATH. Either `--selected llama` triggered ollama install but skipped aider (not auto-installed yet); manually `pipx install aider-chat` on VM.

### Customizing

`VM_HOST` and `VM_PATH` env vars override the defaults:

```bash
VM_HOST=my-other-vm VM_PATH=/srv/bterminal ./tools/smoke_3rd_provider.sh
```

---

## `tools/test_update_vm.sh` — runbook

### Run

```bash
./tools/test_update_vm.sh                  # all 5 phases
./tools/test_update_vm.sh --skip-rollback  # phases 1, 2, 3, 5
./tools/test_update_vm.sh --modes 1,3      # license + e2e pull only
```

### Phases

| # | Step | Pass criteria |
|---|---|---|
| 1 | `_read_local_license` regression (#52) | local + remote license blobs return markdown TEXT, never the path string `defaults/license/LICENSE.en.md` |
| 2 | `_load_local_errata` corruption tolerance | loader returns `[]` on garbage JSON, no exception |
| 3 | End-to-end pull against fake upstream | bare upstream + downgraded clone (VERSION=1.2.0) + push 99.0.0 → `_git_pull_with_autostash` advances working tree |
| 4 | Rollback on mid-install corruption | `false` injected after `[5/7] Installing BTerminal files...` → install exits non-zero, `BTERMINAL_ROLLBACK_OK` emitted, `bterminal/__init__.py` SHA256 matches pre-corruption hash |
| 5 | `_remote_license_blob_path` deep regression (#52) | path resolves to `defaults/license/LICENSE.en.md` AND that path is a real markdown file (not a symlink target) |

### Output

- Per-phase OK / FAIL printed live.
- Logs under `./smoke-logs/update-vm/` (gitignored).
- Final exit 0 only when ALL phases pass.

### Common failure modes

- **Phase 1 fail (local-license-ok missing)**: typically means `_read_local_license` returned a path string — #52 has regressed. Check `bterminal/updater.py` for whether `git show` is still resolving symlinks.
- **Phase 3 fail (pull-ok missing)**: usually a Python ImportError from `_git_pull_with_autostash` being renamed. The pytest source-level checks in `tests/test_update_vm.py` catch this on the host before the VM ever gets a chance — re-run them first.
- **Phase 4 fail (hash drift)**: install.sh's `_on_error` trap didn't restore from `BACKUP_DIR` correctly. Check the prep run finished (`smoke-logs/update-vm/phase4-prep.log`) — without a populated install, there's nothing to back up.
- **Phase 5 fail**: blob path is correct but file missing — usually means a translation slipped past the lint that ensures the English LICENSE.en.md is the source-of-truth.

### Customizing

`VM_HOST` and `VM_PATH` env vars override defaults, same as the other scripts.

---

## When to run

- **Before each release** (after `chore(release): vX.Y.0` commit): full
  smoke pass on a freshly-snapshotted VM. Catches packaging-level
  regressions invisible to unit tests.
- **After install.sh changes**: re-run with `--no-wipe` to verify
  upgrade path works on top of an existing install.
- **After Aider provider changes**: phase 6/7 only — `pytest -k aider`
  on host first, then `./tools/smoke_3rd_provider.sh --skip-llama` to
  validate the integration shell + REST surface.
- **After `bterminal/updater.py` changes**: `pytest tests/test_update_vm.py`
  on host catches helper rename / hash drift; then
  `./tools/test_update_vm.sh --skip-rollback` on the VM exercises
  the live git pull path.
- **After `bterminal/ui/installer_wizard.py` changes**:
  `pytest tests/test_installer_wizard_vm.py` on host catches title /
  header / button-order drift; then
  `./tools/test_installer_wizard_vm.sh --skip-llama` on the VM clicks
  through the GUI under xvfb-run + xdotool.

---

## `tools/test_installer_wizard_vm.sh` — runbook

### Run

```bash
./tools/test_installer_wizard_vm.sh                  # full E2E (~10 min)
./tools/test_installer_wizard_vm.sh --skip-llama     # skip ollama opt-in
./tools/test_installer_wizard_vm.sh --no-postflight  # skip aider/ollama checks
```

### Phases

| # | Step | Pass criteria |
|---|---|---|
| 1 | Spawn wizard under `xvfb-run -a -s '-screen 0 1024x768x24'` on `:99` | Subprocess PID stays alive ≥ 2 s |
| 2 | Wait for page 1 | Wizard log contains `Step 1 of 5: Welcome` OR `xdotool search` finds `BTerminal Installer` window |
| 3 | Page 1 → 2 | Tab×2 + Space (license checkbox) + Tab×2 + Return → page 2 header observed |
| 4 | Page 2 → 3 | Tab×3 + Return → `Step 3 of 5: Pick what to install` observed |
| 5 | Page 3 → 4 | Tab + Space (meld) + Tab + Space (llama) + Tab×3 + Return → page 4 header observed |
| 6 | Page 4 polling | install.sh emits `"phase": "done"` + `"progress": 100` in wizard log within 5 min |
| 7 | Page 5 → close | Return on summary page → wizard PID exits |
| 8 | Post-flight | `~/.local/share/bterminal/bterminal/__init__.py` + `~/.local/bin/bterminal` symlink exist; `aider` in PATH; (soft) `ollama --version` ok + `:11434/api/tags` reachable |

### Common failure modes

- **Phase 2 never reaches page 1**: VM lacks `xvfb-run` / `xdotool` — install via `sudo apt install xvfb xdotool`. Or the wizard imports failed — check `/tmp/bt-installer-wizard.log`.
- **Phase 3 doesn't advance**: button-order assumption broke (someone added a 5th action button); pytest's `test_wizard_action_buttons_layout_unchanged` catches that on the host.
- **Phase 6 timeout**: install.sh hung — usually network (apt/pipx). Tail `/tmp/bt-installer-wizard.log` on the VM. If `--selected llama` chose ollama and `curl | sh` is blocked, retry with `--skip-llama`.
- **Phase 8 ollama API fail**: VM has no D-Bus user session, can't auto-start `ollama serve`. Treated as soft-PASS — manually `nohup ollama serve &` on the VM if real Aider sessions need testing.

### Customizing

`VM_HOST` / `VM_PATH` / `BTERMINAL_VM_TESTS=1` env vars override defaults — same as the other VM scripts.
