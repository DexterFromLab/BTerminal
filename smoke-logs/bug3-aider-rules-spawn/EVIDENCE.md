# BUG#3 evidence — Aider session doesn't receive active rules at spawn

**Captured:** 2026-05-10 18:54-18:57
**VM:** michal-VirtualBox, aider 0.86.2

## Setup

- ctx project: `aider_bug3_test` mapped to `/tmp/aider_ctx_test`
- Active rule (in ctx DB): `"ALWAYS use Polish in code comments"`
- `ctx rules inject aider_bug3_test` returns:
  ```
  ════════════════════════════════════════════════════
  PRZYPOMNIENIE REGUŁ [aider_bug3_test] (co 20 promptów)
  ════════════════════════════════════════════════════
  • ALWAYS use Polish in code comments
  ════════════════════════════════════════════════════
  ```
- Spawn: REST POST /api/sessions/ai → POST /api/tabs/ai/aider

## /proc/PID/cmdline of spawned aider (smoking gun)

`20260510-185428/aider_cmdline.txt`:
```
/home/michal/.local/share/pipx/venvs/aider-chat/bin/python
/home/michal/.local/bin/aider
--model
openai/qwen2.5-coder:0.5b
--openai-api-base
http://localhost:11434/v1
--openai-api-key
dummy
--no-stream
--no-show-model-warnings
/tmp/aider_ctx_test
```

**NO `--read` flag, NO rules file path, NO reference to ctx.** The rules
in the DB are completely invisible to aider's process at spawn. The
PTY-feed mechanism (`_do_inject_rules` in terminal_tab.py) only fires
after `inject_every` (default 20) prompts — for the first 20+ prompts
the rule has zero effect on aider's responses.

## Visual evidence

`20260510-185428/aider_terminal_zoom.png` — BTerminal AiderBug3Test
tab. Banner:
```
Aider v0.86.2
Model: openai/qwen2.5-coder:0.5b with whole edit format
Git repo: .git with 0 files
Repo-map: using 1024 tokens, auto refresh
>
```
No "Added X.md to the chat" line — neither AIDER.md (BUG#2) nor any
rules file. Aider has no awareness of project conventions OR rules.

## Fix sketch (BUG#3 implementation)

In `bterminal/ui/terminal_tab.py:_build_spawn_script` (or new helper
called before `provider.build_argv`):

```python
project_dir = config.get("project_dir")
if project_dir:
    from bterminal.ctx.helpers import _resolve_ctx_project_name
    proj = _resolve_ctx_project_name(project_dir)
    if proj:
        try:
            rules_block = subprocess.run(
                ["ctx", "rules", "inject", proj],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            rules_block = ""
        if rules_block:
            # Per-tab temp file (cleaned on tab close).
            rules_path = (
                f"/tmp/_bt_aider_rules_{os.getpid()}_{id(self)}.md"
            )
            Path(rules_path).write_text(rules_block)
            opts = config.setdefault("provider_options", {})
            opts["rules_file"] = rules_path
            self._owned_rules_files.append(rules_path)  # cleanup
```

Then in `bterminal/providers/aider.py:build_argv`, after the existing
flag handling:

```python
opts = config.get("provider_options") or config
rules_file = opts.get("rules_file")
if rules_file and os.path.isfile(rules_file):
    argv.extend(["--read", rules_file])
```

Tab-close handler (in TerminalTab) already needs to clean per-session
artifacts (chat history etc.) — extend it to unlink each
`_owned_rules_files` entry.

## Pin tests (regression guard)

`tests/e2e/test_aider_rules_at_spawn.py` — 4 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_build_argv_adds_read_for_rules_file_in_provider_options` | **FAIL** | provider contract guard |
| `test_build_argv_no_read_when_rules_file_absent_in_options` | PASS | sanity (no spurious --read) |
| `test_spawn_pipeline_materializes_rules_and_passes_to_argv` | **FAIL** | spawn integration guard |
| `test_real_aider_cmdline_contains_read_for_rules_file` | SKIP | VM-gated behavioural |

After fix lands: 2 failing tests must flip to PASS.

## Existing test affected

The regression suite (#2) already showed
`test_aider_rules_inject_fires_after_inject_every_threshold` failing
in `tests/e2e/test_aider_full_session.py:473`. That test uses the
PTY-feed path. Once BUG#3 is fixed via spawn-time --read, the
PTY-feed path becomes secondary (rules already in context from
prompt #1) and that test may need to be updated or repurposed:
- Option A: keep PTY-feed for periodic re-reminders + relax that
  test's tight coupling.
- Option B: deprecate PTY-feed entirely in favor of --read on
  spawn + ctx-changed events that re-spawn or re-feed.

Whichever path is taken, the new pin tests in
`test_aider_rules_at_spawn.py` lock the spawn-time contract.
