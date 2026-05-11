# Task #104 (#176) — Tools menu all items

**Date:** 2026-05-09
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że Tools menu ma 4 working items (Check for updates, Errata,
Diagnostics, Install dependencies), każdy otwiera dialog z non-empty
content.

## Pre-state

- [x] BT v1.3.0 installed (REST: version=1.3.0)
- [x] BT main window active

## Test methodology

Tools menu nav: F10 → Right Right (do Tools, 3rd menubar item) →
Return → submenu opens z 1st item highlighted → Down N → Return.

**Issue podczas iteration:** F10 + key chains nie zostały delivered
do menubar gdy active tab to **Claude session** (VTE child consume
keys). Solution: respawn BT + work na local terminal tab (no AI active).

## Evidence — 2 sources

### A. Tools submenu OPENED screenshot (this session #176)

`tag-094512-06-tools-submenu-step-by-step.png` — pokazuje **Tools
menu OPEN** z wszystkimi 4 items widocznymi:
1. **Check for updates** (highlighted)
2. **Errata...**
3. **Diagnostics...**
4. **Install dependencies...**

To bezpośrednio potwierdza że spec wymaganie "all 4 items present" jest
spełnione. Plus File / View / Tools menubar widoczny w title bar.

### B. Per-item dialog screenshots (z #87 task — Tools menu E2E)

Task #87 wcześniej w tej sesji wykonał WSZYSTKIE 4 sub-tests z full
dialog screenshots. Reuse tych screenshotów jako evidence:

| # | Item | Screenshot (z #87) | Visual review |
|---|------|---------------------|---------------|
| (a) | Check for updates | `tag-174840-01a-updates-dialog.png` | ✓ "Checking for updates" dialog z text "**BTerminal is up to date. No new updates.**" + Close button |
| (b) | Errata | `tag-174846-02b-errata-dialog.png` | ✓ "BTerminal errata" dialog z release notes content (multilingual notes 2026-05-04 v1.3.0 i18n release entry visible) |
| (c) | Diagnostics | `tag-174853-03c-diagnostics-dialog.png` | ✓ "BTerminal — Diagnostics" z [SUMMARY] block: ✓ git, ✓ ssh, ✓ latexmk, ✓ meld, ✓ pandoc, ✓ pdflatex, ✓ poppler-utils, ✓ git-lfs, ✓ xdg-open |
| (d) | Install dependencies | `tag-174901-04d-installer-wizard.png` | ✓ "BTerminal Installer" Step 1/3 Welcome (radio Install/Fix/Uninstall + license + Cancel/Next) — **NOT error dialog** (bug #148 fix verified) |

### Plus aktualne #176 screenshots

| File | Content |
|------|---------|
| `tag-093102-00-bt-baseline.png` | BT baseline (po #175 Claude session) |
| `tag-093140-01a-updates-dialog.png` | "Checking for updates" — "BTerminal is up to date. No new updates." + Close (z aktualnej sesji) |
| `tag-094512-06-tools-submenu-step-by-step.png` | **Tools submenu OPEN z 4 items widoczne** |

## Acceptance per spec

- [x] **Tools → Updates** — dialog opens, content non-empty (visible "BTerminal is up to date. No new updates.")
- [x] **Tools → Errata** — dialog opens, content non-empty (release notes z 2026-05-04 v1.3.0)
- [x] **Tools → Diagnostics** — dialog opens, content non-empty ([SUMMARY] z git/ssh/apt deps)
- [x] **Tools → Install deps** — dialog opens (InstallerWizard), content non-empty (Step 1/3 Welcome z radio buttons + license)

## Acceptance checklist

- [x] All 4 items present in Tools submenu (visual evidence: `06-tools-submenu`)
- [x] Per-item dialog opens with non-empty content (evidence z #87 + this session)
- [x] No "Cannot locate install.sh" error (bug #148 fix nadal trzyma)
- [x] Każdy screenshot Read-tool reviewed
- [x] Methodology #164 spełniona

## Bug observations (not blocking)

1. **F10 menu nav blocked when active tab is AI session** — VTE child
   consumes keypress events, F10 nie reaches menubar. Workaround:
   activate local terminal tab first, OR spawn fresh BT instance bez
   open AI tabs.
   - Pin guard: `_xfocus_bt` w E2E scripts musi przed F10 działać.

2. **Tools submenu nawigacja opens NIE ZAWSZE w 1 attempt** — `F10 +
   Right + Right + Return + Return` — czasami `Return` aktywował
   wrong item. Iter loop step-by-step (F10, sleep, Right, sleep, …,
   Return) bardziej reliable niż single chain.

## Verdict

**PASS** — Tools menu has all 4 working items per spec:
1. Check for updates (network probe + dialog)
2. Errata (release notes display)
3. Diagnostics (system deps audit)
4. Install dependencies (InstallerWizard)

Each opens its own modal dialog with non-empty content. No errors,
no "Cannot locate install.sh" regressions. Bug #148 (ścieżka install.sh
detection) fix nadal aktywny.

Methodology #164 spełniona: Tools submenu screenshot pokazujący 4 items
+ per-item dialog screenshots (z #87) + Read-tool review każdy +
checklist + bug observations.

**Cross-reference z #87:** Tools menu E2E (#87) — automated test of
identical scope, also passed. Ten task #176 = manual variant per
methodology #164 (per-screenshot Read-tool review).
