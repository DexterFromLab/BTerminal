# BUG#10 evidence — Pull failed dialog leaks raw ANSI escape codes

**Captured:** 2026-05-10 19:25
**Test:** `tests/e2e/test_pull_failed_no_ansi_codes.py`
**Method:** Subprocess mock + simulated ollama failure stream

## Visual evidence

`user_reported_screenshot.png` (`copied_images/65cccf12d2a7.png`,
manual QA): Pull failed dialog filled with garbled cursor codes:

> ?2026h ?25l ?1Gpulling manifest ?K ?25h ?2026l
> ?2026h ?25l ?1Gpulling manifest ?K ?25h ?2026l
> [...repeating...]
> Error: pull model manifest: file does not exist

The actual error is the LAST line. Everything above is `ollama
pull`'s TTY progress animation captured byte-for-byte.

## Empirical proof (mocked subprocess)

The behavioral test feeds a realistic ANSI-loaded fixture through
`pull_model`:
```
\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest \x1b[K\x1b[?25h\x1b[?2026l
[...5x more progress redraws...]
Error: pull model manifest: file does not exist
```

Output from `pull_model("doesnotexist")` — RAW, no stripping:
```
\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest \x1b[K\x1b[?25h\x1b[?2026l
[same content as input — pure passthrough]
Error: pull model manifest: file does not exist
```

Patterns confirmed leaking: `\x1b[`, `?2026h`, `?25l`, `[1G`, `[K`.

## Source-level proof

`bterminal/ollama_client.py:273-289`:
```python
def pull_model(name: str, timeout: float = 600.0) -> tuple[bool, str]:
    if not is_cli_installed():
        return False, "ollama CLI not installed"
    try:
        result = subprocess.run(
            ["ollama", "pull", name],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"subprocess failed: {exc}"
    if result.returncode == 0:
        return True, "pulled successfully"
    return False, (result.stderr or result.stdout).strip()
        # ^^^^^^ raw output — no ANSI strip, no parsing
```

`subprocess.run(..., capture_output=True)` captures the bytes
ollama emits, INCLUDING the cursor-control sequences ollama uses
for its progress redraw (because ollama detects no TTY and falls
back to text progress, but still emits cursor moves).

## Fix recipe (BUG#10 implementation)

Add to `bterminal/ollama_client.py` (module level):
```python
import re

_ANSI_RE = re.compile(r"\x1b\[[\d;?]*[a-zA-Z]")

def _strip_ansi(text: str) -> str:
    """Remove CSI sequences from ollama progress output. Covers
    the patterns ollama 0.x uses for its non-TTY fallback redraw:
    ?2026h/l (synchronized output), ?25l/h (cursor hide/show),
    1G (cursor to col 1), K (erase line)."""
    return _ANSI_RE.sub("", text)
```

In `pull_model` body:
```python
if result.returncode == 0:
    return True, "pulled successfully"
raw = (result.stderr or result.stdout)
return False, _strip_ansi(raw).strip()
```

After fix, behavioral test will see:
```
pulling manifest pulling manifest pulling manifest ..pulling manifest ..
Error: pull model manifest: file does not exist
```
(progress text concatenated, but readable — and the meaningful
error line preserved.)

For BUG#11 (which asks to also map known errors to friendly text)
the strip is still the first step — friendly mapping happens after.

## Pin tests (regression guard)

`tests/e2e/test_pull_failed_no_ansi_codes.py` — 5 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_ollama_client_has_ansi_strip_helper` | **FAIL** | helper must exist |
| `test_pull_model_strips_ansi_before_return` | **FAIL** | helper must be called |
| `test_strip_helper_removes_all_known_escape_patterns` | SKIP | activates after helper exists |
| `test_pull_model_returns_clean_message_on_simulated_failure` | **FAIL** | end-to-end behavioral guard |
| `test_pull_model_preserves_meaningful_error_line` | PASS | sanity — strip keeps the actual error line |

After fix lands: 3 FAILs flip PASS + SKIP activates → 5/5 green.

## Cross-reference

- BUG#11 (Pull failed parsing into user-friendly text) — depends
  on this strip happening first. Combined fix: strip → parse known
  error patterns → map to PL message → return.
- BUG#12 (Pull failed PL title) — same dialog, separate i18n issue.
