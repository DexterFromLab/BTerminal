# BUG#11 evidence — Pull failed dialog dumps full stdout instead of friendly message

**Captured:** 2026-05-10 19:30-19:33
**VM:** michal-VirtualBox, ollama 0.x
**Test:** `tests/e2e/test_pull_failed_friendly_message.py`

## Visual evidence (real VM screenshot)

`vm/pull_failed_dialog_v2_zoom.png` — programmatically opened the
Pull failed dialog after running `ollama_client.pull_model("ddd",
timeout=15.0)` against the real ollama daemon on VM.

Visible content:
- Title: **"Pull failed"** (English — also BUG#12)
- Body shows 8 garbled lines:
  ```
  ?2026h ?25l ?1Gpulling manifest ?K ?25h ?2026l ?2026h ?25l ?
  2026h ?25l ?1Gpulling manifest ?K ?25h ?2026l ?2026h ?
  25l ?1Gpulling manifest .. ?K ?25h ?2026l ?2026h ?
  25l ?1Gpulling manifest .. ?K ?25h ?2026l ?2026h ?
  25l ?1Gpulling manifest .. ?K ?25h ?2026l ?2026h ?
  25l ?1Gpulling manifest .. ?K ?25h ?2026l ?2026h ?
  25l ?1Gpulling manifest ?K ?25h ?2026l
  Error: pull model manifest: file does not exist
  ```
- OK button

Bug confirmed live: user has to scroll past 7 lines of cursor-codes
to reach the actual error.

## Empirical message length

Real `pull_model("ddd")` output on VM (PL locale, real ollama):
```
ok = False
msg_len = 478 characters
```

Acceptance threshold: < 200 chars. Currently **478 chars**, of which
~430 are progress-redraw noise.

`vm/raw_pull_ddd_output.txt` has the full transcript.

## Source-level proof

`bterminal/ollama_client.py:289`:
```python
return False, (result.stderr or result.stdout).strip()
```

No parsing. No mapping to friendly strings. No length cap. The user
gets the exact bytes ollama emitted.

## Behavioral test results (mocked subprocess)

`test_friendly_error_helper_exists` FAIL — no helper exists
`test_pull_model_uses_friendly_error_helper` FAIL — pull_model doesn't call any
`test_model_not_found_message_mentions_model_name_and_is_short` FAIL — 478 chars dump
`test_message_does_not_contain_full_progress_dump` FAIL — multiple 'pulling manifest'
`test_daemon_not_running_message_is_actionable` PASS — coincidentally raw msg has 'connection refused'
`test_disk_full_message_mentions_space` PASS — coincidentally raw msg has 'no space'
`test_unknown_error_falls_back_to_last_error_line` FAIL — full dump returned

The two passing tests are vacuous (raw passthrough happens to
include the keyword); after fix they'll remain green but become
meaningful (actual mapped strings).

## Fix recipe (BUG#11 implementation)

Add to `bterminal/ollama_client.py` (depends on BUG#10's
`_strip_ansi`):

```python
def _friendly_pull_error(stderr: str, model_name: str) -> str:
    """Map known ollama error patterns to short, actionable messages."""
    cleaned = _strip_ansi(stderr).strip()

    # Pattern 1: model not found (the most common case — typos)
    if "pull model manifest: file does not exist" in cleaned \
       or "manifest unknown" in cleaned:
        return _("Model {name!r} nie istnieje w bibliotece Ollama. "
                 "Sprawdź pisownię na ollama.com/library.").format(
                     name=model_name)

    # Pattern 2: daemon offline
    if "connection refused" in cleaned \
       or "dial tcp" in cleaned and "11434" in cleaned:
        return _("Daemon Ollama nie jest uruchomiony. "
                 "Wykonaj: systemctl --user start ollama")

    # Pattern 3: disk full
    if "no space left on device" in cleaned:
        return _("Brak miejsca na dysku. "
                 "Zwolnij miejsce w ~/.ollama/models/ przed pull.")

    # Pattern 4: timeout
    if "context deadline exceeded" in cleaned \
       or "i/o timeout" in cleaned:
        return _("Timeout pobierania {name!r}. Sprawdź połączenie "
                 "internetowe.").format(name=model_name)

    # Fallback: extract the LAST `Error:` line, truncate to 180 chars
    error_lines = [l for l in cleaned.splitlines()
                   if l.strip().startswith("Error:")]
    if error_lines:
        return error_lines[-1][:180]

    # Ultimate fallback: first 180 chars of cleaned output
    return cleaned[:180] if cleaned else _("Pull failed (no output)")
```

In `pull_model`:
```python
if result.returncode == 0:
    return True, "pulled successfully"
raw = (result.stderr or result.stdout)
return False, _friendly_pull_error(raw, name)
```

After fix, behavioral test for "ddd" returns:
```
Model 'ddd' nie istnieje w bibliotece Ollama. Sprawdź pisownię na ollama.com/library.
```
~80 chars, model name present, action hint included.

## Pin tests (regression guard)

`tests/e2e/test_pull_failed_friendly_message.py` — 7 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_friendly_error_helper_exists` | **FAIL** | helper must exist |
| `test_pull_model_uses_friendly_error_helper` | **FAIL** | helper must be called |
| `test_model_not_found_message_mentions_model_name_and_is_short` | **FAIL** | core BUG#11 contract |
| `test_message_does_not_contain_full_progress_dump` | **FAIL** | progress noise filter |
| `test_daemon_not_running_message_is_actionable` | PASS (vacuous) | becomes meaningful after fix |
| `test_disk_full_message_mentions_space` | PASS (vacuous) | becomes meaningful after fix |
| `test_unknown_error_falls_back_to_last_error_line` | **FAIL** | unknown-error fallback contract |

After fix lands: 5 FAILs flip PASS, 2 vacuous PASS become meaningful → 7/7 green.

## Cross-reference

- BUG#10 (ANSI strip) — prerequisite for this fix; `_strip_ansi`
  must exist first
- BUG#12 (Pull failed PL title) — same dialog, separate i18n
  layer (handled by next task)
- BUG#8 (dropdown) — once dropdown lands, typo-driven errors
  drop ~zero, but the parser is still needed for daemon/disk/
  network failures
