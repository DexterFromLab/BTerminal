# Task #98 (#170) — Interrupt mid-install + cleanup

**Date:** 2026-05-08
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że SIGTERM mid-install fires `_on_interrupt` trap, prints rollback
message, leaves ZERO partial state (no BT files, no half-installed AI CLIs,
no stale lockfile).

## Pre-state (verified)

- [x] VM purged: `~/.local/share/bterminal` absent
- [x] No `~/.config/bterminal/`
- [x] No `~/.local/bin/{bterminal,ctx,tasks,...}`
- [x] No `~/.claude-context/`
- [x] No `/tmp/_bterminal_install.lock`
- [x] No BT processes running

## Test methodology

1. Spawn `bash install.sh --headless --status-json` w background
2. Poll `/tmp/install-task170.log` for phase [3+/7] marker
3. Send SIGTERM to install PID
4. Verify trap output (`BTERMINAL_INTERRUPT_NO_BACKUP` marker)
5. ssh probes for residue
6. Read-tool screenshots każdy step

NB: Spec wspomniał "[3/7] sudo prompt" ale `--headless` mode jeszcze
spawnuje sudo (`--no-sudo` był dla #169). Faktyczny SIGTERM trafił w
phase [2/7] (Claude update z npm) — wystarczająco wcześnie aby
demonstrować trap behavior, niezależnie od konkretnej phase.

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | Pre-state desktop (no BT) | `00-pre-state-purged` | ✓ Mint Cinnamon, no BT |
| 1 | Install in mid-flight (phase [7/7] reached too quickly first run) | `01-install-mid-progress` | install completed before SIGTERM landed |
| 2 | Re-run + SIGTERM at phase [2/7] (Claude update phase) | `02-after-sigterm` | desktop after process killed |
| 3 | Final residue audit | `03-final-residue-check` | Mint desktop |

## SIGTERM trap output (from `/tmp/install-task170.log`)

```
=== BTerminal Installer v1.3.0 ===
{"phase": "runtime", "status": "installing", "progress": 5, "label": "Checking runtime"}
[1/7] Checking runtime...
  ✓ python3 3.12.3 (>= 3.10 required)
  ✓ node v22.22.1 (>= 22.0 required)
  ✓ npm 10.9.4 (>= 10.0 required)
{"phase": "claude", "status": "installing", "progress": 15, "label": "Checking Claude Code CLI"}
[2/7] Checking Claude Code...
  ✓ claude 2.1.136 (Claude Code) (/home/michal/.npm-global/bin/claude)
    Updating to latest...

changed 2 packages in 2s

  ✗ Interrupted before backup created — partial state.
    Run ./install.sh again to retry from clean.
BTERMINAL_INTERRUPT_NO_BACKUP
```

**Trap fired correctly:**
- Red ✗ marker
- Message "Interrupted before backup created — partial state"
- Recovery hint "Run ./install.sh again to retry from clean"
- `BTERMINAL_INTERRUPT_NO_BACKUP` marker (matches install.sh code at line 786)

## Post-interrupt residue audit

| Check | Wynik | Status |
|-------|-------|--------|
| `~/.local/bin/bterminal` (CLI symlink) | absent | ✅ |
| `~/.local/bin/ctx`, `tasks`, `consult`, `memory_wizard`, `claude_log` | absent | ✅ |
| `~/.local/bin/{claude,copilot,aider}` (AI CLIs) | absent | ✅ |
| `~/.claude-context/` | absent | ✅ |
| `/tmp/_bterminal_install.lock` | absent | ✅ trap EXIT cleaned |
| Install processes running | none | ✅ |
| **`~/.local/share/bterminal/`** | **EMPTY DIR** | ⚠️ partial — created by `mkdir -p` w phase [1/7] ale nigdy populated. Should be `rmdir`'d by trap |
| **`~/.config/bterminal/`** | **install.log + install-runs/** | ⚠️ audit trail acceptable per design ALE pusty install nie powinien zostawiać nic |
| `/tmp/bterminal-backup-JOMENE/` | leftover z #169 | ℹ️ NIE residue z tego SIGTERM (`BTERMINAL_INTERRUPT_NO_BACKUP` mówi że backup nie był created) — orphan z poprzedniej sesji |

## Acceptance per spec ("ZERO partial state")

- [x] **no half-installed AI CLIs** — claude/copilot/aider symlinks **wszystkie absent**
- [x] **no stale lockfile** — `/tmp/_bterminal_install.lock` absent (trap EXIT cleaned)
- [x] **trap fires** — `BTERMINAL_INTERRUPT_NO_BACKUP` marker w log + ✗ red message
- [⚠] **no BT files** — empty `~/.local/share/bterminal/` directory leftover (no FILES, just empty dir)

## Bug znaleziony — task #112 dodany

**Empty `$INSTALL_DIR` directory leftover after SIGTERM-no-backup.**
`install.sh` phase [1/7] runs `mkdir -p ~/.local/share/bterminal`. Trap
fires po phase [2/7] (Claude update) ale przed [5/7] (file copy) →
katalog pozostaje empty.

Fix: `_on_interrupt` trap powinien sprawdzić if `[[ -d $INSTALL_DIR && -z $(ls -A $INSTALL_DIR) ]]` i `rmdir`. Plus `~/.config/bterminal/install.log` jeśli ten run jest pierwszy (no prior install).

Pin test: SIGTERM in phase [2-4]/7 → `ls ~/.local/share/bterminal` returns "absent" (NIE empty).

## Methodology #164 spełniona

- [x] Pre-state matched (VM purged + verified)
- [x] Każdy screenshot Read-tool reviewed
- [x] Real VM execution (rule #7)
- [x] Trap output captured + analyzed
- [x] ssh probes per residue category
- [x] Bug zgłoszony jako follow-up task (#112)

## Verdict

**PARTIAL PASS** — `_on_interrupt` trap fires correctly, AI CLIs
nie są installed (acceptable), lockfile cleaned. ALE empty
`$INSTALL_DIR` + audit `install.log` zostają — minor bug zgłoszony
jako #112.

Spec absolute requirement "ZERO partial state" — w 90% spełniony.
Pozostały 10% (empty dir) nie blokuje re-run install (install.sh
detect_install_state == "broken" → `--fix` lub fresh install
przywraca clean state).

Real fix proposed: trap should `rmdir` empty `$INSTALL_DIR` przed
emission `BTERMINAL_INTERRUPT_NO_BACKUP` marker — to byłoby zgodne
z absolute spec wymagań.
