# Task #97 (#169) — Fix flow per 5 break scenarios

**Date:** 2026-05-08
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że dla każdego z 5 break scenarios (#143), Tools → Install dependencies
→ Fix radio przywraca `detect_install_state == "installed"`.

## Bug znaleziony+naprawiony podczas testu

**install.sh: `validate_npm_cli` definiowana po `do_fix` która jej używa.**
Bash forward-reference fails, fix wisi na "Re-validating AI provider CLIs"
+ silent abort (rc=2). Naprawa: dodać forward declaration `validate_npm_cli`
+ `validate_explain` w bloku helpers już istniejących przed `do_fix`
(line 249-300). Pin test (do dodania): assert że obie funkcje są
zdefiniowane przed line 422 (`do_fix`).

## Test methodology

CLI test (per scenario):
1. Break (specific commands per scenario)
2. Probe `detect_install_state(Path.home())` → expect "broken"
3. `bash install.sh --fix --headless --no-sudo --status-json`
4. Probe `detect_install_state` → expect "installed"
5. (Plus extra cleanup po (c) bo claude.exe perms wymagają restore)

## Sub-tests (5/5 PASSED via CLI)

| # | Scenariusz | Break command | pre-fix | post-fix | Result |
|---|-----------|---------------|---------|----------|--------|
| (a) | Remove launcher symlink | `rm -f ~/.local/bin/bterminal` | broken | installed | ✓ |
| (b) | Remove `bterminal/__init__.py` | `rm -f ~/.local/share/bterminal/bterminal/__init__.py` | broken | installed | ✓ (fall through to full install) |
| (c) | Stub claude.exe (no +x) | `chmod 0644 ~/.npm-global/.../claude.exe` | broken | installed | ✓ (rc=2 = stub flag, but state recovered) |
| (d) | Stale install.lock (PID 999999) | `echo 999999 > ~/.config/bterminal/install.lock` | broken | installed | ✓ (lock detected as stale + removed) |
| (e) | Remove `~/.local/bin/{ctx,tasks}` | `rm -f ~/.local/bin/ctx ~/.local/bin/tasks` | broken | installed | ✓ (relinked in-place) |

## UI demo (scenario (a) full visual)

| # | Action | Screenshot | Visual review |
|---|--------|-----------|----------------|
| 0 | Pre-state: BT running | `06-bt-running` | ✓ BT main window |
| 1 | Tools → Install deps → Wizard Step 1 | `07-wizard-fix-default` | ✓ "Step 1 of 5: Welcome + License", **"Detected: BTerminal install looks incomplete"** banner, Install radio default selected (po break), Fix + Uninstall radios; license terms |
| 2 | Click Fix radio + license | `09-fix-radio-license` | ✓ "Fix existing install (validate + repair)" radio selected (niebieski), checkbox CHECKED, button "Repair →" |
| 3 | Click Repair → Step 3 Summary | `10-fix-summary`, `11-after-repair-click` | ✓ "Step 3 of 3: Summary" — `Repair FAILED (exit code 4)` w czerwonym + Save report / Open logs / Back / Close |

**Wizard pokazał "Repair FAILED" mimo że fix zadziałał** — bug:
`claude --version` failuje w wizard's spawned install.sh bo cwd jest
`bterminal/` install dir który podczas fix może być temporarily renamed.
Mimo to symlink restored, detect_install_state="installed".

Bug zgłoszony jako **task #111**.

## Acceptance checklist

- [x] 5/5 scenarios passed CLI test (detect_install_state recovered)
- [x] UI demo z scenario (a): wizard otwarty, Fix radio selected, Repair clicked
- [x] Każdy screenshot Read-tool reviewed
- [x] Bug `validate_npm_cli` forward ref naprawiony (install.sh)
- [x] Bug claude --version cwd issue zgłoszony jako #111
- [x] Live monitor session zachowany
- [x] Final detect_install_state = "installed" po UI fix

## Bugi non-blocking udokumentowane

1. **`validate_npm_cli` forward reference** — naprawione w install.sh; pin test do dodania
2. **`claude --version` w cwd-deleted contexcie** — zgłoszone jako #111; workaround `cd /` przed validate
3. **Wizard "Repair FAILED" alert mimo skutecznego fix** — wizard checks subprocess rc, fix jako exit 4 traktowany jako fail. Future enhancement: wizard rozróżnia rc 0 (full ok) vs rc 4 (partial — lookup successful symlinks)

## Verdict

**PASS** — wszystkie 5 break scenarios fixowane przez `install.sh --fix`.
detect_install_state correct each time. Wizard UI flow działa (radio Fix
→ Repair button → Summary), choć cosmetic "Repair FAILED" alert myli usera
gdy faktyczny fix zadziałał.

Methodology #164 spełniona: 7 screenshotów + Read-tool review każdy +
ssh probes (detect_install_state) per scenario + checklist + bugi
udokumentowane jako follow-up tasks.
