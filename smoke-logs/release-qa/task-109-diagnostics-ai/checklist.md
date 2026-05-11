# Task #109 — Diagnostics dialog AI Providers section

**Date:** 2026-05-09
**Status:** PASS (code + pin tests + host smoke)

## Code changes

### `bterminal/diagnostics.py`

1. **New dataclass** `AIProviderStatus(name, label, present, path, version)`
2. **New constant** `_AI_PROVIDER_LOCATIONS` — fallback paths per provider
   (mirrors install.sh's `find_claude_bin/find_copilot_bin` lookup chain)
3. **New constant** `_AI_PROVIDER_LABELS` — display names
4. **New `_detect_ai_provider(name)`** — probes binary + version (5s timeout)
5. **New `audit_ai_providers(names=("claude","copilot","aider"))`** — bulk audit
6. **`format_summary_text(statuses, ai_statuses=None)`** — optional 2nd arg
   appends "AI Providers:" section z install hints dla missing providers:
   - claude → `npm install -g @anthropic-ai/claude-code`
   - copilot → `npm install -g @github/copilot`
   - aider → `pipx install aider-chat`

### `bterminal/app.py`

`_show_diagnostics_dialog()` calls `audit_ai_providers()` + passes
`ai_statuses=` to `format_summary_text()`. Dialog body now includes
3 AI provider rows.

## Pin tests (9/9 ✓ — `tests/test_diagnostics_ai_providers.py`)

- `test_ai_provider_status_dataclass_fields`
- `test_audit_ai_providers_returns_three_providers_by_default`
- `test_audit_ai_providers_custom_names`
- `test_format_summary_text_legacy_no_ai_section` (backward-compat)
- `test_format_summary_text_with_ai_section`
- `test_format_summary_text_missing_provider_shows_install_hint` (aider/pipx)
- `test_format_summary_text_missing_claude_shows_npm_hint`
- `test_format_summary_text_missing_copilot_shows_npm_hint`
- `test_app_diagnostics_dialog_uses_ai_section` (regression guard)

## Host smoke (real audit)

```
✓ claude     /home/bartek/.local/bin/claude  2.1.132 (Claude Code)
✗ copilot    (missing)
✓ aider      /home/bartek/.local/bin/aider   aider 0.86.2

AI Providers:
  ✓ Claude Code                  (/home/bartek/.local/bin/claude, 2.1.132)
  ✗ GitHub Copilot CLI           — not installed (npm install -g @github/copilot)
  ✓ Aider (local LLM)            (/home/bartek/.local/bin/aider, aider 0.86.2)
```

Section appears correctly w summary text. Missing copilot daje
install hint `npm install -g @github/copilot`.

## VM live test

VM REST :7780 nie wstaje od osobnego issue (BT spawn cycle flakiness
z poprzednich task'ów #178/#179 — niezwiązane z #109 zmianami).
Pin tests + host smoke wystarczające evidence.

## Verdict

PASS — Diagnostics dialog teraz pokazuje AI Providers section z
✓/✗ per provider + version + install hints. Mirror install.sh's
`find_*_bin` lookup chain → użytkownik widzi ten sam status w
runtime co install-time.
