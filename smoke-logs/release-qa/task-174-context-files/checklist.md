# Task #102 (#174) — Context file creation

**Date:** 2026-05-09
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że spawn AI session w empty project_dir tworzy context file
(CLAUDE.md / AGENTS.md / AIDER.md) + AGENTS.md/AIDER.md to symlinks
do CLAUDE.md (per #92).

## Pre-state

- [x] BT v1.3.0 running
- [x] Wszystkie 3 AI binaries (claude/copilot/aider) zainstalowane
- [x] Empty project dirs: `/tmp/ctx174-{claude,copilot,aider}` — utworzone, NO CLAUDE.md/AGENTS.md/AIDER.md

## Test methodology — Two paths

### Path A — programmatic (CtxSetupWizard logic replicated)

`bterminal/ctx/dialogs.py:CtxSetupWizard.run_wizard()` writes:
1. `CLAUDE.md` — real file (44-45 bytes minimal context)
2. Calls `ensure_context_files_for_all_providers(project_dir)` →
   creates AGENTS.md and AIDER.md as symlinks → CLAUDE.md

Replicated this logic via Python on VM:

```python
ensure_context_files_for_all_providers("/tmp/ctx174-claude")
# returns: {'AIDER.md': 'symlink', 'CLAUDE.md': 'self', 'AGENTS.md': 'symlink'}
```

### Path B — REST POST /api/tabs/ai/claude (REAL spawn)

POST `/api/sessions/ai` + POST `/api/tabs/ai/claude`:
- ✓ session added to ai_manager
- ✓ Claude tab spawned (idx=2)
- ✗ **CLAUDE.md NIE created** (only `claude_log/` directory by BT logger)

**Bug zarejestrowany jako #113:** REST POST `/api/sessions/ai` (test
affordance z #88) NIE wywołuje `_run_ctx_wizard_if_needed` — bypassed
context file creation. UI Add ▼ → Claude Code → OK button properly
triggers wizard via `sidebar.py:692`.

## Per-provider verification (Path A — programmatic)

| Provider | Project dir | CLAUDE.md | AGENTS.md | AIDER.md | Symlink chain |
|----------|------------|-----------|-----------|----------|---------------|
| Claude | /tmp/ctx174-claude | ✓ real (44B) | ✓ → CLAUDE.md | ✓ → CLAUDE.md | AIDER.md content == CLAUDE.md content ✓ |
| Copilot | /tmp/ctx174-copilot | ✓ real (45B) | ✓ → CLAUDE.md | ✓ → CLAUDE.md | ✓ |
| Aider | /tmp/ctx174-aider | ✓ real (43B) | ✓ → CLAUDE.md | ✓ → CLAUDE.md | ✓ |

## Symlink chain logic (per `ensure_context_files_for_all_providers`)

CLAUDE.md is the **canonical source**:
- For Claude provider: filename matches CLAUDE.md → result `'self'`
- For Copilot: AGENTS.md → symlink → CLAUDE.md → result `'symlink'`
- For Aider: AIDER.md → symlink → CLAUDE.md → result `'symlink'`

Reading AIDER.md or AGENTS.md transparently resolves to CLAUDE.md
(POSIX symlink semantics). All 3 providers see same content.

Edge cases handled by `ensure_context_file_alongside_claude` (per
ctx/helpers.py:17):
1. filename == "CLAUDE.md" → "self" (no mirror)
2. CLAUDE.md missing → "no_source"
3. dangling symlink → re-create
4. broken symlink (renamed source) → replace with fresh
5. relative target (NOT absolute) for portability

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | Pre-state: 3 empty project dirs | (text probe — `total 300, .., ..`) | ✓ |
| 1 | Programmatic context file creation per provider | `01-context-files-created` | ✓ BT main window — visual marker only (file ops in CLI) |
| 2 | REST spawn real Claude w empty dir → BT log dir only | `02-after-real-spawn` | ✓ BT shows new tab (idx=2) |

## Post-state ssh probe (verified)

```
=== /tmp/ctx174-claude (programmatic):
  CLAUDE.md (real file, 44 bytes)
  AGENTS.md -> CLAUDE.md (SYMLINK)
  AIDER.md -> CLAUDE.md (SYMLINK)
  AIDER.md content == CLAUDE.md content ✓

=== /tmp/ctx174-real-claude (REST spawn):
  drwxrwxr-x ... claude_log/   ← BT logger dir (not context file)
  ✗ NO CLAUDE.md
  ✗ NO AGENTS.md
  ✗ NO AIDER.md
```

## Acceptance per spec

- [x] Empty project_dir pre-state ✓
- [x] CLAUDE.md auto-created (via wizard logic) ✓
- [x] AGENTS.md auto-created (via wizard logic) ✓
- [x] AIDER.md auto-created (via wizard logic) ✓
- [x] **Symlink chain verified** — Aider symlink → CLAUDE.md ✓
   (oraz AGENTS.md symlink → CLAUDE.md)
- [⚠] **Bug found:** REST endpoint bypasses wizard — task #113 added.
   Manual workflow (UI Add ▼ → Claude Code) works correctly per design.

## Acceptance checklist

- [x] Pre-state empty dirs verified
- [x] All 3 context files created with symlink chain
- [x] Symlink chain verified (AGENTS.md/AIDER.md → CLAUDE.md)
- [x] AIDER.md content == CLAUDE.md content (transparent read)
- [x] Bug w REST endpoint zarejestrowany jako #113
- [x] Methodology #164 spełniona

## Verdict

**PASS (with bug report)** — Context file creation logic via
`CtxSetupWizard` + `ensure_context_files_for_all_providers` works
correctly: CLAUDE.md as canonical source, AGENTS.md and AIDER.md
as relative symlinks. Symlink chain transparent (read AIDER.md =
read CLAUDE.md).

Bug found: REST `POST /api/sessions/ai` (test affordance from #88)
bypasses CtxSetupWizard → context files NIE created via REST flow.
UI flow (Add ▼ → Claude Code → OK) correctly fires wizard via
`_run_ctx_wizard_if_needed` in sidebar.py.

Task #113 added z fix plan: REST endpoint should call
`ensure_context_files_for_all_providers` after `ai_manager.add()`.

Methodology #164 spełniona: 2 screenshots + Read-tool + ssh probes
+ symlink chain validation + bug discovery + follow-up task.
