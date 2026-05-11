# BUG#8 evidence — Pull Ollama dialog requires manual model name typing

**Captured:** 2026-05-10 19:21
**Test:** `tests/e2e/test_pull_ollama_dialog_dropdown.py`
**Method:** xvfb + Gtk.Dialog.run interception + tree walk

## Visual evidence

`user_reported_screenshot.png` (`copied_images/d9050a3d490b.png`,
manual QA 2026-05-10):

> Title: "Pull Ollama model"
> Label: "Model name (e.g. qwen2.5-coder:0.5b, llama3.1:8b):"
> Entry: empty
> Buttons: [Anuluj] [Pull]

User has zero affordance to discover available model names. Has to
type from memory. Typo → minutes-long failed pull (see BUG#10
"file does not exist" cascade).

## Behavioural empirical capture

xvfb driver intercepted `Gtk.Dialog.run` to walk the tree before
the dialog destroys itself. Result:

```json
{
  "dialog_captured": true,
  "comboboxes": 0,
  "combo_entries": [],
  "linkbuttons": [],
  "entries": 1
}
```

Confirms:
- 0 ComboBox of any kind
- 0 LinkButton (no library link)
- Just 1 Entry (free-form text input — necessary but not sufficient)

## Source-level proof

`bterminal/ui/dialogs/options.py:_on_pull_model` (lines 571-608):
- `Gtk.Dialog(title=_("Pull Ollama model"))`
- One `Gtk.Label` (the "Model name (e.g. ...)" hint)
- One `Gtk.Entry` (placeholder = recommended tag from system_probe)
- Cancel + Pull buttons

No ComboBox. No LinkButton. No URL reference to ollama.com/library.

## Fix sketch (BUG#8 implementation)

In `bterminal/ui/dialogs/options.py:_on_pull_model`, replace the
`Gtk.Entry` portion with a hybrid ComboBoxText + Entry:

```python
# Curated list — order by descending recommendation strength
CURATED_MODELS = [
    "qwen2.5-coder:7b",         # primary aider recommendation
    "qwen2.5-coder:3b",         # smallest still-usable for aider
    "deepseek-coder-v2:16b",    # heavyweight, RAM-permitting
    "codellama:7b",             # fallback general coder
    "llama3.1:8b",              # general-purpose, multi-task
    "qwen2.5:14b",              # general-purpose, larger
    "llava:13b",                # vision-capable
]

# ComboBoxText with editable Entry — selects from list OR types custom
combo = Gtk.ComboBoxText.new_with_entry()
for tag in CURATED_MODELS:
    combo.append_text(tag)
combo.set_active(0)  # default to first
box.pack_start(combo, False, False, 0)

# Library link button below
lib_btn = Gtk.LinkButton.new_with_label(
    "https://ollama.com/library",
    "Browse all models on ollama.com →",
)
box.pack_start(lib_btn, False, False, 0)
```

In the response handler:
```python
if dlg.run() == Gtk.ResponseType.OK:
    name = combo.get_active_text() or rec_hint
    name = name.strip()
    dlg.destroy()
    self._pull_model_blocking(name, ollama_client)
```

`Gtk.ComboBoxText.new_with_entry()` gives both: dropdown for known
tags + free-form Entry for new releases. Best of both worlds.

## Pin tests (regression guard)

`tests/e2e/test_pull_ollama_dialog_dropdown.py` — 6 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_dialog_constructs_combobox_for_model_selection` | **FAIL** | static: source must reference Gtk.ComboBoxText |
| `test_dialog_combobox_has_at_least_5_curated_options` | **FAIL** | static: ≥5 curated tags |
| `test_dialog_includes_link_button_to_ollama_library` | **FAIL** | static: LinkButton + ollama.com URL |
| `test_dialog_keeps_entry_for_custom_input` | PASS | sanity — Entry survives |
| `test_behavioural_dialog_has_combobox_with_5plus_entries` | **FAIL** | runtime tree confirms ComboBox |
| `test_behavioural_dialog_has_library_link_button` | **FAIL** | runtime tree confirms LinkButton |

After fix lands: all 6 must pass.

## Cross-reference

- BUG#4 (no <3B model warning) — same dialog. Both fixes can land
  in one PR: ComboBoxText with curated list + size-guard helper
  on the picked tag.
- BUG#9 (Pull dialog stringi nieprzetłumaczone) — same dialog. PL
  catalog needs entries for "Pull Ollama model", "Pull",
  "Model name…", and the new "Browse models on ollama.com →"
  link label.
- BUG#10/#11 (Pull failed parsing) — once dropdown lands, typo-
  driven failures should drop to ~zero, but the parsing fix is
  still needed for genuine network/disk errors.
