# BUG#2 evidence — Aider session doesn't load AIDER.md context

**Captured:** 2026-05-10 18:44-18:48
**VM:** michal-VirtualBox, aider 0.86.2

## Visual evidence

`20260510-184429/aider_terminal_zoom.png` — BTerminal tab "AiderBug2Test"
spawned via REST POST. project_dir = `/tmp/aider_ctx_test` containing
AIDER.md with marker text "ELEPHANT-CASTLE-9527".

Banner shown:
```
Aider v0.86.2
Model: openai/qwen2.5-coder:0.5b with whole edit format
Git repo: .git with 0 files
Repo-map: using 1024 tokens, auto refresh
>
```

**Brak linii** `Added AIDER.md to the chat (read-only).` mimo że plik
istnieje w project_dir.

## Direct CLI evidence (proves aider's contract)

`aider_WITHOUT_read_BUG.txt` — aider invoked WITHOUT --read in same dir:
```
Aider v0.86.2
…
Repo-map: using 1024 tokens, auto refresh
                                          ← ENDS HERE, no AIDER.md mention
```

`aider_WITH_read_FIXED.txt` — aider invoked WITH --read AIDER.md:
```
Aider v0.86.2
…
Repo-map: using 1024 tokens, auto refresh
Added AIDER.md to the chat (read-only).   ← THE LINE THAT'S MISSING IN BT
```

This proves:
1. aider 0.86.2 does NOT auto-discover AIDER.md from cwd (contrary to
   the docstring in `tests/test_aider_context_file.py:5-7`).
2. The fix shape is `--read <path>` — when given, aider explicitly
   announces the load.

## Root cause

`bterminal/providers/aider.py:build_argv` (line 121-202) constructs:
```
argv = [aider, --model, …, --openai-api-base, …, --openai-api-key, …,
        --no-stream, --no-show-model-warnings, <project_dir>]
```

There is NO logic that detects AIDER.md or CLAUDE.md in `project_dir`
and appends `--read <path>`. The positional `<project_dir>` is the
spawn cwd, but that alone doesn't trigger conventions loading.

## Fix recipe (next session, BUG#2 implementation)

In `build_argv`, after the existing flag handling but before the
positional `project_dir` arg:
```python
project_dir = config.get("project_dir")
if project_dir:
    # Auto-attach context file as read-only.
    for fname in ("AIDER.md", "CLAUDE.md"):
        ctx_path = os.path.join(project_dir, fname)
        if os.path.isfile(ctx_path):
            argv.extend(["--read", ctx_path])
            break  # one ctx file is enough; AIDER.md preferred
    argv.append(project_dir)
```

Order: AIDER.md first (provider-native), CLAUDE.md fallback. Stop at
first match because they're typically symlinks (per #113).

## Pin tests (regression guard)

`tests/e2e/test_aider_context_read_flag.py` — 6 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_build_argv_adds_read_flag_when_AIDER_md_in_project_dir` | **FAIL** | regression guard (catches BT not adding --read) |
| `test_build_argv_falls_back_to_CLAUDE_md_when_no_AIDER_md` | **FAIL** | regression guard (catches missing fallback) |
| `test_build_argv_no_read_when_project_dir_has_no_context_files` | PASS | sanity (no false-positive --read) |
| `test_build_argv_no_read_when_project_dir_absent` | PASS | sanity |
| `test_real_aider_banner_includes_added_AIDER_md_with_read_flag` | PASS | locks in aider's contract: --read works |
| `test_real_aider_banner_LACKS_added_AIDER_md_without_read_flag` | PASS | locks in aider's contract: no auto-discovery |

After fix lands: top 2 must flip to PASS; bottom 4 keep PASS. Total 6/6.
