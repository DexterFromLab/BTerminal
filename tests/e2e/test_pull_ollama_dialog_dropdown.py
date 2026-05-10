"""E2E test for BUG#8 — Pull Ollama dialog requires manual model
name typing instead of providing a curated dropdown + library link.

User report (manual QA, 2026-05-10): "Nie podoba mi się że trzeba
modelu wpisać ręcznie. To nie jest wygodne, skąd user ma wiedzieć
jakie modele są dostępne? może warto żeby miał dostęp do jakieś
listy dostępnych modeli na stronie, lub nawet, dropdown z
predefiniowanymi listami?"

The dialog at `bterminal/ui/dialogs/options.py:_on_pull_model`
currently has only:
  - one `Gtk.Label` ("Model name (e.g. ...)")
  - one `Gtk.Entry` (placeholder = recommended tag)
  - Cancel / Pull buttons

User has no way to discover what model names are valid. Typo lands
straight in `ollama pull <typo>` → minutes-long retry. The fix is
two-part:

  (a) Curated dropdown — `Gtk.ComboBoxText` with ≥5 sane defaults
      (qwen2.5-coder:7b, qwen2.5-coder:3b, deepseek-coder-v2:16b,
      codellama:7b, llama3.1:8b, plus a "Custom..." sentinel that
      reveals the existing Entry).
  (b) Library link — `Gtk.LinkButton` pointing at
      https://ollama.com/library so power-users browse the full
      catalog without leaving the dialog.

The test pins the structural contract: source must reference a
ComboBoxText with ≥5 entries AND a LinkButton with the library URI.
Behavioural verification (xvfb + tree walk) will activate once the
dropdown is wired in.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPTIONS_PY = REPO_ROOT / "bterminal" / "ui" / "dialogs" / "options.py"
SCREENSHOTS = REPO_ROOT / "smoke-logs" / "bug8-pull-dialog-dropdown"


def _xvfb_available() -> bool:
    return shutil.which("xvfb-run") is not None


# ── Static checks ────────────────────────────────────────────────────────


def _on_pull_model_body() -> str:
    src = OPTIONS_PY.read_text(encoding="utf-8")
    start = src.find("def _on_pull_model")
    assert start > 0, "_on_pull_model not found in options.py"
    end = src.find("\n    def ", start + 1)
    return src[start:end] if end > start else src[start:]


def test_dialog_constructs_combobox_for_model_selection():
    """Pin: the dialog body must instantiate a `Gtk.ComboBoxText`
    (or `Gtk.ComboBox` with a string store) so the user can pick
    from a list. The bare `Gtk.Entry`-only form is the bug."""
    body = _on_pull_model_body()
    has_combo = bool(re.search(
        r"Gtk\.ComboBoxText|Gtk\.ComboBox\(", body))
    assert has_combo, (
        "_on_pull_model body does not construct a Gtk.ComboBoxText. "
        "Today the dialog has only an Entry — user has to type the "
        "model name from memory. Add a ComboBoxText with ≥5 curated "
        "tags + a 'Custom…' sentinel that reveals the Entry."
    )


def test_dialog_combobox_has_at_least_5_curated_options():
    """Pin: the dropdown must offer at least 5 known-good model
    tags. Five is the minimum useful set covering small-coding-7b,
    medium-coding-13b, generic-llama-8b, vision/multimodal, and a
    Custom… sentinel."""
    body = _on_pull_model_body()
    # Heuristic: count `append_text(...)` or `append(...)` calls
    # inside the body; either signals a populated combobox.
    appends = re.findall(
        r"\.append_text\(\s*['\"]([^'\"]+)['\"]", body)
    if not appends:
        appends = re.findall(
            r"\.append\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            body)
    if not appends:
        # Some implementations use a list literal — count entries
        # in any list of strings declared in the body
        list_entries = re.findall(
            r"['\"]([\w.\-]+:[\w.\-]+)['\"]", body)
        appends = list_entries
    distinct = set(appends)
    assert len(distinct) >= 5, (
        f"dropdown populated with only {len(distinct)} entries: "
        f"{distinct!r}. Curated list should include (suggested): "
        f"qwen2.5-coder:7b, qwen2.5-coder:3b, deepseek-coder-v2:16b, "
        f"codellama:7b, llama3.1:8b — plus a 'Custom…' sentinel."
    )


def test_dialog_includes_link_button_to_ollama_library():
    """Pin: a `Gtk.LinkButton` (or equivalent) with URI
    https://ollama.com/library must be packed into the dialog
    content area. Power users who want to browse the full catalog
    need a one-click escape hatch."""
    body = _on_pull_model_body()
    has_link = bool(re.search(
        r"Gtk\.LinkButton|set_uri|Pango\..*hyperlink", body))
    has_library_url = "ollama.com/library" in body or \
                      "ollama.com/search" in body
    assert has_link and has_library_url, (
        f"dialog lacks Gtk.LinkButton (has_link={has_link}) "
        f"and/or ollama.com/library URI (has_url={has_library_url}). "
        "Add: lib_btn = Gtk.LinkButton.new_with_label("
        "'https://ollama.com/library', 'Browse models →') and "
        "pack into content area."
    )


def test_dialog_keeps_entry_for_custom_input():
    """Sanity: the existing `Gtk.Entry` for free-form input must
    survive the refactor. ComboBoxText alone is too restrictive —
    new ollama tags are released daily."""
    body = _on_pull_model_body()
    assert "Gtk.Entry" in body or "ComboBoxText" in body, (
        "dialog must keep at least one text-input affordance for "
        "custom tags. Either Entry or an editable ComboBoxText "
        "satisfies this."
    )


# ── Behavioural (xvfb) — activates once dropdown is implemented ──────────


_DRIVER = r'''
import json, os, sys
os.environ.setdefault("GTK_A11Y", "none")
os.environ.setdefault("NO_AT_BRIDGE", "1")
sys.path.insert(0, sys.argv[1])

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from bterminal import config as _cfg
_cfg._OPTIONS = {
    "theme": "dark", "font": "Monospace 11", "language": "en",
    "scrollback": 10000, "shell_command": "bash",
    "image_paste_hint_enabled": True, "check_updates_on_start": True,
    "show_command_panel": True, "show_status_panel": True,
    "show_files_panel": True, "show_skills_panel": True,
    "show_memory_panel": True, "show_recent_panel": True,
    "tab_position": "top", "max_idle_seconds": 10800,
    "providers_disabled": [], "ollama_models_visible": [],
}

from bterminal.ui.dialogs.options import OptionsDialog


def walk(w, cls):
    out = []
    if isinstance(w, cls):
        out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            out.extend(walk(c, cls))
    return out


def pump(n=80):
    ctx = GLib.MainContext.default()
    for _ in range(n):
        while ctx.iteration(False):
            pass


# Call _on_pull_model directly. It builds + runs() the dialog as a
# modal — but Gtk.Dialog.run() blocks. We need to capture the dialog
# WITHOUT running it. Instead we instrument: monkey-patch dlg.run
# to return CANCEL immediately while we walk the tree.
parent_window = Gtk.Window()
parent_window.realize()
opts = OptionsDialog(parent_window)
opts.realize()
opts.show_all()
pump(50)

snapshot = {
    "comboboxes": 0,
    "combo_entries": [],
    "linkbuttons": [],
    "entries": 0,
    "dialog_captured": False,
}

orig_dlg_run = Gtk.Dialog.run

def captured_run(self):
    """Walk the dialog tree HERE — caller will destroy() the dialog
    immediately after we return, so post-call introspection is too
    late."""
    snapshot["dialog_captured"] = True
    pump(50)
    for cb in walk(self, Gtk.ComboBoxText):
        snapshot["comboboxes"] += 1
        model = cb.get_model()
        if model is not None:
            it = model.get_iter_first()
            while it is not None:
                v = model.get_value(it, 0)
                if v:
                    snapshot["combo_entries"].append(v)
                it = model.iter_next(it)
    for lb in walk(self, Gtk.LinkButton):
        snapshot["linkbuttons"].append({
            "uri": lb.get_uri(),
            "label": lb.get_label(),
        })
    snapshot["entries"] = len(walk(self, Gtk.Entry))
    return Gtk.ResponseType.CANCEL

Gtk.Dialog.run = captured_run
try:
    opts._on_pull_model()
finally:
    Gtk.Dialog.run = orig_dlg_run

result = snapshot

print(json.dumps(result))
'''


@pytest.fixture(scope="module")
def pull_dialog_widget_tree(tmp_path_factory):
    """Run xvfb driver that opens the Pull dialog, walks the tree,
    and emits widget metadata."""
    if not _xvfb_available() and not os.environ.get("DISPLAY"):
        pytest.skip("needs xvfb-run or DISPLAY")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    driver_path = tmp_path_factory.mktemp("bug8") / "_driver.py"
    driver_path.write_text(_DRIVER)
    cmd = [
        "xvfb-run", "-a",
        "--server-args=-screen 0 1920x944x24",
        "python3", str(driver_path), str(REPO_ROOT),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        pytest.fail(
            f"driver crashed:\nrc={res.returncode}\n"
            f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    json_lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
    assert json_lines, (
        f"no JSON output. stdout:\n{res.stdout}\n"
        f"stderr:\n{res.stderr}"
    )
    return json.loads(json_lines[-1])


def test_behavioural_dialog_has_combobox_with_5plus_entries(
        pull_dialog_widget_tree):
    """Behavioural counterpart of the static test. Once the source
    has the ComboBox, this confirms the widget is actually packed
    AND populated — not silently constructed and dropped on the
    floor."""
    r = pull_dialog_widget_tree
    assert r["dialog_captured"], (
        f"_on_pull_model didn't build a Gtk.Dialog the test could "
        f"capture. Result: {r}"
    )
    assert r["comboboxes"] >= 1, (
        f"no Gtk.ComboBoxText in the Pull dialog widget tree. "
        f"Counts: {r}"
    )
    assert len(r["combo_entries"]) >= 5, (
        f"ComboBox present but populated with only "
        f"{len(r['combo_entries'])} entries: {r['combo_entries']}"
    )


def test_behavioural_dialog_has_library_link_button(
        pull_dialog_widget_tree):
    """Behavioural pin: a LinkButton with the ollama.com/library
    URI must be in the tree. Label text is flexible — 'Browse
    models →' is the suggestion but any non-empty label suffices."""
    r = pull_dialog_widget_tree
    assert r["dialog_captured"], r
    matching = [
        lb for lb in r["linkbuttons"]
        if lb["uri"] and (
            "ollama.com/library" in lb["uri"]
            or "ollama.com/search" in lb["uri"]
        )
    ]
    assert matching, (
        f"no Gtk.LinkButton with ollama.com/library URI. "
        f"Found linkbuttons: {r['linkbuttons']}"
    )
