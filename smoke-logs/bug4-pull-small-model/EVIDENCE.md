# BUG#4 evidence — Pull Ollama dialog accepts <3B models without warning

**Captured:** 2026-05-10 19:00-19:04
**VM:** michal-VirtualBox, BT v1.3.0

## Source-level proof

`bterminal/ui/dialogs/options.py:571-608` — the entire `_on_pull_model`
flow:

```python
def _on_pull_model(self):
    dlg = Gtk.Dialog(title=_("Pull Ollama model"), …)
    dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
    dlg.add_button(_("Pull"), Gtk.ResponseType.OK)

    lbl = Gtk.Label(…)
    rec_hint = (recs[0]["ollama_tag"] if recs else "qwen2.5-coder:0.5b")
    lbl.set_markup(f"Model name (e.g. <tt>{rec_hint}</tt>, "
                   f"<tt>llama3.1:8b</tt>):")
    box.pack_start(lbl, False, False, 0)

    entry = Gtk.Entry()
    entry.set_placeholder_text(rec_hint)
    …
    if dlg.run() == Gtk.ResponseType.OK:
        name = entry.get_text().strip() or rec_hint
        dlg.destroy()
        self._pull_model_blocking(name, ollama_client)   ← FIRES PULL DIRECTLY
    else:
        dlg.destroy()
```

There is **no model-size validation** between `entry.get_text()` and
`_pull_model_blocking`. Whatever string the user types — `0.5b`, `1b`,
`3b`, gibberish — is passed straight to `ollama pull`.

The hint label even includes `qwen2.5-coder:0.5b` as the **default
fallback rec_hint** when system_probe returns no recommendations,
actively encouraging the broken case.

## Visual evidence

`20260510-190043/options_dialog_full.png` — the Options dialog itself
(not the Pull sub-dialog) is shown in this artifact. **Bonus finding**
related to BUG#5 / #7: dialog content area renders EMPTY in this run
— title "Opcje BTerminal" and "Anuluj"/"Zapisz" footer are present
but the body shows no Theme/Font/Local Models sections. This may be
a transient render issue OR confirmation of the layout problem
covered in BUG#5/#7. Documented here for cross-reference.

The Pull-sub-dialog couldn't be opened cleanly via xdotool because
of this empty-content issue, but **the static source proof above is
sufficient**: even WITHOUT a screenshot, we know the dialog has no
warning because the code has no warning logic.

## Real-world consequences (BUG#4 already burned the user)

From the original BUG#2/#3 manual QA:
```
Aider v0.86.2
Model: openai/qwen2.5-coder:0.5b with whole edit format

> hello
[…]
The LLM did not conform to the edit format.
[…repeated infinitely…]
```

The user picked `qwen2.5-coder:0.5b` as their default Ollama model
because:
- BT's default rec_hint is `qwen2.5-coder:0.5b` (literal string in
  options.py:590)
- Pull dialog has no size warning
- Aider then fails to follow edit format on every single message

Even if BUG#2/#3 were fixed (rules + AIDER.md reaching aider), this
0.5B model would STILL be unable to use them productively — the LLM
is too small to follow structured-output instructions reliably.

## Fix sketch (BUG#4 implementation)

Add to `bterminal/ui/dialogs/options.py` (module level):

```python
import re

_TAG_SIZE_RE = re.compile(r":(\d+(?:\.\d+)?)([bm])$", re.IGNORECASE)

def _model_param_count_b(tag: str) -> float | None:
    """Parse param count (in billions) from common ollama tags.
    Returns None if the tag has no recognisable size suffix."""
    m = _TAG_SIZE_RE.search(tag.strip().lower())
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) == "m":
        n /= 1000.0
    return n

_SMALL_MODEL_THRESHOLD_B = 3.0
```

Then in `_on_pull_model`, between `name = entry.get_text()…` and
`self._pull_model_blocking(…)`:

```python
size = _model_param_count_b(name)
if size is not None and size < _SMALL_MODEL_THRESHOLD_B:
    confirm = Gtk.MessageDialog(
        transient_for=self, modal=True,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.YES_NO,
        text=_(f"{name} has only {size}B parameters"),
        secondary_text=_(
            "Models below 3B parameters often fail to follow "
            "Aider's edit format and produce empty / repeated "
            "responses. Are you sure you want to pull this "
            "model?"),
    )
    response = confirm.run()
    confirm.destroy()
    if response != Gtk.ResponseType.YES:
        return  # user backed out
self._pull_model_blocking(name, ollama_client)
```

Plus update the dialog label to include a hint:

```python
lbl.set_markup(
    f"Model name (e.g. <tt>{rec_hint}</tt>, "
    f"<tt>llama3.1:8b</tt>):\n"
    f"<small>Note: models below 3B params may fail with Aider's "
    f"edit format.</small>")
```

And bump the rec_hint default away from 0.5b — `qwen2.5-coder:7b`
or `llama3.1:8b` is a saner fallback.

## Pin tests (regression guard)

`tests/e2e/test_ollama_pull_small_model_guard.py` — 5 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_helper_function_parses_common_ollama_tag_sizes` | **FAIL** | requires `_model_param_count_b` (or similar) symbol |
| `test_helper_handles_known_tags_correctly` | SKIP | activates once helper exists |
| `test_on_pull_model_consults_size_helper_before_pulling` | **FAIL** | dialog must call helper |
| `test_on_pull_model_uses_warning_dialog_for_small_models` | SKIP | activates once helper referenced |
| `test_pull_dialog_label_warns_about_small_models` | **FAIL** | label hint missing |

After fix lands: all 5 must pass (3 FAILs flip to PASS + 2 SKIPs activate to PASS).
