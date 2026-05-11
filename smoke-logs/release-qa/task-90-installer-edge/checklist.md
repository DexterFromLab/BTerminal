# Task #90 (#162) — Installer edge cases

**Date:** 2026-05-08

---

## Deliverable

`tools/test_installer_edge_vm.sh` — auto-driver dla 5 hostile-condition
scenarios. Każdy bez ryzyka mutacji prawdziwego VM home (HOME redirected
to /tmp scratch dirs).

## Sub-tests (5/5 PASS na real VM)

| # | Scenariusz | Mechanizm w install.sh | Wynik |
|---|------------|-------------------------|-------|
| (a) | Offline / `--no-sudo` mode | apt phase guarded by `[[ NO_SUDO ]] && return` | ✓ completed without `Running: sudo` |
| (b) | SIGTERM mid-install | `trap '_on_interrupt' INT TERM` → BACKUP_DIR restore | ✓ trap fired (BTERMINAL_INTERRUPT marker) |
| (c) | Corrupt LICENSE.md (0 bytes) | install treats license as live symlink | ✓ no Traceback/syntax errors |
| (d) | Read-only target dir | `mkdir` fails cleanly → `_on_error` → `BTERMINAL_FRESH_INSTALL_FAILED` | ✓ permission error caught + clean failure marker |
| (e) | Parallel install (flock race) | `flock -n 9` on `$INSTALL_LOCKFILE` | ✓ 2nd refused, 0 tracebacks |

## Bugi znalezione+naprawione podczas iteracji

1. **MANUAL install hints contain "sudo apt install …" as docs**
   - `--no-sudo` mode prints user-facing instructions z `sudo apt install
     git-lfs` jako tekst. Naive `grep "sudo apt"` matched both real
     calls AND docs strings → false negative w (a).
   - Fix: bardziej restrykcyjny pattern `'^Running: sudo|^\$ sudo apt'`
     który matchuje tylko shell prompts/log markers.

2. **SIGTERM nie zadziałał z `--no-sudo --headless`**
   - Pierwsza iteracja: install kończył się szybciej niż my zdążyliśmy
     wysłać SIGTERM (--no-sudo skip apt = phases [2-3]/7 prawie
     instantowe).
   - Fix: poll-loop czeka na `[4-7]/7` phase markers (npm install,
     file copy) zanim wyśle SIGTERM. Te phases zajmują kilka sekund
     nawet w `--no-sudo` mode.

3. **`grep -c file1 file2` outputs `file1:0\nfile2:0`**
   - Multi-file grep -c wyświetla per-file count z prefiksem.
     `[[ "$count" == "0" ]]` failuje bo "0\n0" != "0".
   - Fix: `cat file1 file2 | grep -c PATTERN` daje single integer.
   - Pin test `test_script_uses_combined_grep_for_count_sums`.

4. **`|| echo 0` fallback dorzucał drugie 0**
   - `_vm "grep -c X /file" || echo 0` — gdy grep zwraca "0\n" exit 1
     (no match), `|| echo 0` dorzuca "0" → "0\n0".
   - Fix: `; true` zamiast `|| echo 0` — propaguje 0 exit bez
     dodatkowego output.

5. **Polish locale Permission denied**
   - VM ma polski locale → `mkdir: Brak dostępu` zamiast `Permission
     denied`. Fix: matcher accepts oba + `BTERMINAL_FRESH_INSTALL_FAILED`
     marker (locale-agnostic).
   - Pin test `test_script_locale_agnostic_error_check`.

## Pin tests — 12/12 ✓ (`tests/test_installer_edge_e2e.py`)

| Test | Pin |
|------|-----|
| Script structure (4) | exists, executable, bash -n, all 5 headers |
| `test_script_uses_safe_test_isolation` | HOME=/tmp scratch, never real $HOME |
| `test_script_locale_agnostic_error_check` | PL/EN compatibility |
| `test_script_distinguishes_running_sudo_from_doc_strings` | strict pattern |
| `test_script_uses_combined_grep_for_count_sums` | cat-then-grep idiom |
| `test_install_sh_has_flock_guard` | `flock -n 9` + `BTERMINAL_INSTALL_LOCKED` |
| `test_install_sh_has_signal_trap` | `trap _on_interrupt INT TERM` |
| `test_install_sh_has_rollback_marker` | `BTERMINAL_INTERRUPT` + `FRESH_INSTALL_FAILED` |
| `test_install_sh_has_no_sudo_mode` | `--no-sudo` flag |
| `test_install_sh_emits_summary_block` | `[SUMMARY]` / `installed successfully` |

Combined regression: **227/227** zielono.

## Evidence (per-scenario log tails w `*-tail.log`)

- `01a-offline-tail.log` — install completed bez sudo invocation
- `01b-sigterm-tail.log` — trap fired output po SIGTERM
- `01c-license-tail.log` — install completed mimo zerowej LICENSE.md
- `01d-rohome-tail.log` — `mkdir: Brak dostępu` + `BTERMINAL_FRESH_INSTALL_FAILED`
- `01e-flock-tail.log` — "Another install.sh is already running" + `BTERMINAL_INSTALL_LOCKED`

## install.sh gaps zidentyfikowane (nie regresja, info)

- **Brak explicit pre-check network connectivity** — install.sh nie
  pinguje internet; offline failure manifestuje się jako apt/curl
  fail. `--no-sudo` mode jest workaround. Future enhancement: wczesny
  `curl --max-time 3 https://www.google.com` check w sudo phase.
- **Brak pre-verify LICENSE.md size** — install akceptuje 0-byte
  license i tworzy symlink (BT runtime widzi pustą license, użytkownik
  klika Accept na pustym dialogu). Future: `[[ -s LICENSE.md ]]` test.
- **Brak pre-check `$HOME` writability** — install próbuje mkdir, error
  "Brak dostępu" jest user-friendly ale nie ma dedicated message
  ("Cannot write to $HOME — check permissions"). Future enhancement.

## Verdict

**5/5 PASS na real VM**, 12/12 pin tests. install.sh udowodnione że
NIE crashuje w żadnym z 5 hostile scenarios; każdy fail wygląda na
"clean exit z marker'em" zamiast traceback. Gaps zidentyfikowane jako
**enhancements** (nie blokery).

Helpers cumulative (#157-#162): F10 nav, `_xfocus_bt`, `_rest`, live
monitor, REST endpoints (`/api/sessions/*`, `/api/window/state`),
edge case test patterns. Gotowe dla #163 (uninstaller scenarios) i
Release QA #170 (manual interrupt).
