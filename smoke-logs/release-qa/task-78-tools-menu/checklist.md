# Task #78 (#150) — Tools menu E2E + AI sessions
## Evidence Checklist (Release QA Process #164)

**Date:** 2026-05-08
**VM state pre-test:** BT installed (v1.3.0), claude 2.1.133 + copilot 1.0.43 OK, Aider not yet (patch #77 requires fresh reinstall)
**Methodology:** master task #164 — every step screenshot-validated

---

## ✓ Tools menu (4/4 sub-tests)

### (a) Tools → Check for updates ✓
- **Evidence:** `screenshots/03c-updates-dialog.png`
- **Visual:** Modal dialog "Checking for updates" — body **"BTerminal is up to date. No new updates."** + Close button
- **Acceptance:** dialog opens within 5s, no error shown ✓

### (b) Tools → Errata ✓
- **Evidence:** `screenshots/04-errata-dialog.png`
- **Visual:** "BTerminal errata" dialog with multiline release-notes content (2026-05-04 v1.3.0 i18n release entry visible)
- **Acceptance:** content non-empty, multilingual notes rendered ✓

### (c) Tools → Diagnostics ✓
- **Evidence:** `screenshots/05-diagnostics-dialog.png`
- **Visual:** "BTerminal — Diagnostics" with `[SUMMARY]` block:
  - Required: ✓ git, ✓ ssh
  - Auto-install (apt): ✓ latexmk, ✓ meld, ✓ pandoc, ✓ pdflatex, ✓ poppler-utils
  - Optional: ✗ git-lfs, ✓ xdg-open
- **Acceptance:** full audit table visible, all expected rows present ✓

### (d) Tools → Install dependencies ✓
- **Evidence:** `screenshots/06d-pre-return-install-deps.png` (item highlighted) + `06e-install-deps-wizard.png` (wizard open)
- **Visual:** InstallerWizard "Step 1 of 3: Welcome — repair existing install" with radio buttons:
  - Install BTerminal (greyed out — already installed)
  - **Fix existing install** (default — selected)
  - Uninstall BTerminal
- **Acceptance:** Wizard opens (NOT error dialog "Cannot locate install.sh") — confirms bug #148 fixed ✓

---

## Partial: AI session spawn

### Add AI Session dialog — Claude Code provider ✓
- **Evidence:** `screenshots/08b-ai-session-dialog.png`
- **Visual:** Modal "Add AI Session" with AI Provider dropdown set to **Claude Code**, fields: Name/Folder/Project dir/Custom prompt/Plugins, Cancel + OK buttons
- **Acceptance:** dialog opens via File → New Claude Code session ✓

### AI session spawn (Connect to existing session)
- **Status:** UI flow incomplete in this run — sidebar right-click did not surface context menu via xdotool mouse-click coordinates (possibly window-relative offset miscalc).
- **Disposition:** AI session spawn flow is covered explicitly by tasks **#161** (E2E auto), **#171** (Claude QA), **#172** (Copilot QA), **#173** (Aider QA — requires re-install per #77).
- **Verified separately:**
  - `~/.local/bin/claude --version` → `2.1.133 (Claude Code)` ✓
  - `~/.local/bin/copilot --version` → `GitHub Copilot CLI 1.0.43.` ✓
  - `~/.local/bin/aider` → ✗ (patch #77 in source, VM needs reinstall)

---

## Bonus findings during this test

1. **Bug #148 (repo_path) confirmed FIXED** — Tools → Install dependencies now opens the wizard (no "Cannot locate install.sh" error). Repo path written by install.sh's line 1552 reads back correctly via `bterminal.config.REPO_DIR`.
2. **Wizard correctly detects current install state** — radio "Fix existing install" is pre-selected (not "Install" — which is greyed out because BT is already installed). Means `detect_install_state()` returns "installed" and welcome page logic works.
3. **Mnemonic Alt+T NOT working for Tools menu** — only F10 + arrow navigation works. (Minor UX issue, separate task could be filed.)

---

## Verdict

**4/4 Tools menu sub-tests passed with screenshot evidence.** Bug #148 verified fixed in flow. AI session spawn partial — full coverage in tasks #161/#171/#172/#173.
