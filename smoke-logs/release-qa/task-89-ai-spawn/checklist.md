# Task #89 (#161) — AI session spawn E2E per provider

**Date:** 2026-05-08

---

## Deliverable

`tools/test_ai_spawn_vm.sh` — driver dla 3 AI providers × spawn+close
flow z REST asercjami + xdotool screenshots.

## Sub-tests (7/7 PASS na real VM)

Spec mówił o "6 sub-tests" (3 providers × spawn+close); skrypt dodatkowo
asercjuje log-clean (no FATAL/Traceback markers) jako 7-szy.

| # | Action | Wynik |
|---|--------|-------|
| 1 | claude spawn → tab idx=1, tabs 1→2 | ✓ |
| 2 | claude close (force=true) → tabs 2→1 | ✓ |
| 3 | copilot spawn → tab idx=1, tabs 1→2 | ✓ |
| 4 | copilot close → tabs 2→1 | ✓ |
| 5 | aider spawn (mock_ai_cli) → tab idx=1, tabs 1→2 | ✓ |
| 6 | aider close → tabs 2→1 | ✓ |
| 7 | Final log assertion: NO FATAL/Traceback markers | ✓ |

Per provider asercje inline:
- `tab.provider == saved provider` (NIE fallback do default)
- Feed `echo hello\n` przyjęte (11 bytes)
- BT log nie zawiera "command not found" markerów

## Bugi znalezione+naprawione

1. **Close wymaga `?force=true` przy AI tab**
   - AI spawn rejestruje `active task 'e2e-<provider>'` per tab
   - Plain `POST /api/tabs/<idx>/close` → 400 "tab has active task; pass ?force=true"
   - Fix: `?force=true` w close URL
   - Pin test `test_script_uses_force_close_flag`

2. **Aider binary missing na VM** (per task #77 reinstall pending)
   - Detect → symlink `~/.local/bin/aider` → `tools/mock_ai_cli`
   - Test verifies SPAWN+CLOSE flow (provider-agnostic), nie aider-specific
     output
   - Cleanup-at-exit usuwa symlink żeby VM nie został z mock'iem
   - Pin test `test_script_handles_missing_aider_via_mock`

## Pin tests — 12/12 ✓ (`tests/test_ai_spawn_e2e.py`)

| Test | Pin |
|------|-----|
| `test_script_exists_and_executable` | binary + chmod +x |
| `test_script_passes_bash_syntax_check` | bash -n |
| `test_script_tests_all_3_providers` | claude/copilot/aider |
| `test_script_tests_spawn_and_close_per_provider` | 6 sub-tests |
| `test_script_uses_force_close_flag` | `?force=true` + comment why |
| `test_script_feeds_echo_hello_per_provider` | feed /api/tabs/<idx>/feed |
| `test_script_screenshots_each_step` | 3 tags × per provider |
| `test_script_handles_missing_aider_via_mock` | mock_ai_cli symlink |
| `test_script_asserts_no_fatal_log_markers` | FATAL/Traceback grep |
| `test_script_verifies_tab_provider_matches_saved` | tab.provider check |
| `test_script_creates_saved_sessions_for_each_provider` | REST seeded fixtures |
| `test_script_cleanup_removes_saved_sessions` | trap EXIT delete loop |

Combined regression: **215/215** zielono.

## Visual evidence (real VM, 10 screenshots)

| File | Shows |
|------|-------|
| `00-baseline.png` | start state |
| `claude-1-after-spawn.png` | E2E_Claude tab z ✦ icon |
| `claude-2-after-feed.png` | **Claude Code workspace prompt**: "/tmp/e2e-claude" trust dialog z "Yes, I trust this folder" — real Claude Code spawned |
| `claude-3-after-close.png` | po close — tylko local terminal |
| `copilot-1-after-spawn.png` | E2E_Copilot tab z 🤖 icon |
| `copilot-2-after-feed.png` | Copilot CLI output |
| `copilot-3-after-close.png` | po close |
| `aider-1-after-spawn.png` | E2E_Aider tab z 🦫 icon |
| `aider-2-after-feed.png` | "MOCK_AI_CLI ready. Type messages, exit with Ctrl-D" — provider-agnostic spawn działa nawet z mock binary |
| `aider-3-after-close.png` | po close |

## VM state pre-test

- claude 2.1.133 ✓ (Claude Code) → real spawn
- copilot 1.0.43 ✓ → real spawn
- aider ✗ missing → mock_ai_cli symlink (auto-cleaned)
- Ollama daemon: not running (irrelevant — aider używa mock)

## Verdict

**7/7 PASS.** Wszystkie 3 providery spawnują się przez REST z
provider-aware code path (capability matrix, intro_prompt, paste
template, stats_bar). Real Claude Code + real Copilot + provider-agnostic
mock dla Aider — pełna provider abstraction confirmed.

`?force=true` discovery udokumentowane pin testem żeby się nie powtórzył.

Helpers cumulative (#157-#161) + REST endpoints (`/api/sessions/*`,
`/api/window/state`) gotowe dla #162 (extra installer scenarios) i
Release QA #171/172/173 (per-provider manual prompts).
