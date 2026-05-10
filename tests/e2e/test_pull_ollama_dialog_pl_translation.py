"""E2E test for BUG#9 — Pull Ollama dialog strings not translated
to Polish.

User report (manual QA, 2026-05-10): with `language: "pl"` selected,
the Pull Ollama dialog still shows English strings:
  - title "Pull Ollama model" instead of "Pobierz model Ollama"
  - button "Pull" instead of "Pobierz"
  - label starting with "Model name (e.g. ...)" instead of PL

Three layers of failure today:

1. **Catalog drift** (same root cause as BUG#1, BUG#6) — the .pot
   was last extracted 2026-05-05; the strings exist in source
   wrapped with `_()` but were added to options.py after that
   date. Without re-running `./tools/i18n.sh extract`, the msgids
   never reach pl/.../*.po and gettext returns msgid (English)
   at runtime.

2. **Untranslatable f-string** at options.py:591-593 — the dialog
   label uses `lbl.set_markup(f"Model name (e.g. <tt>{rec_hint}"
   f"</tt>, <tt>llama3.1:8b</tt>):")` — a Python f-string is NOT
   wrapped in `_()`, so even if the catalog had a "Model name"
   entry, gettext would never see this dynamic string. The fix
   requires an extractable template + format() at use time.

3. **PL msgstr missing** — even after extract, the PL catalog
   needs hand-translated msgstrs for each new msgid.

The test pins all three layers:
  (a) Static: Pull-related msgids exist in .pot AND have non-empty
      PL msgstrs.
  (b) Static: the label uses `_("template")` not a bare f-string.
  (c) Behavioural: dialog rendered with init_locale("pl") shows
      Polish title, Polish Pull button, and a non-English label.
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
PL_PO = REPO_ROOT / "locale" / "pl" / "LC_MESSAGES" / "bterminal.po"
POT = REPO_ROOT / "locale" / "bterminal.pot"
SCREENSHOTS = REPO_ROOT / "smoke-logs" / "bug9-pull-dialog-pl"


def _xvfb_available() -> bool:
    return shutil.which("xvfb-run") is not None


# ── Catalog parser (same shape as BUG#1/#6 tests) ────────────────────────


def _parse_po_msgs(po_path: Path) -> dict[str, str]:
    if not po_path.is_file():
        return {}
    out = {}
    cur_id = cur_str = None
    in_id = in_str = False
    for raw in po_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            if cur_id is not None:
                out[cur_id] = cur_str or ""
            cur_id = _unquote(line[len("msgid "):])
            cur_str = ""
            in_id, in_str = True, False
        elif line.startswith("msgstr "):
            cur_str = _unquote(line[len("msgstr "):])
            in_id, in_str = False, True
        elif line.startswith('"') and line.endswith('"'):
            chunk = _unquote(line)
            if in_id:
                cur_id = (cur_id or "") + chunk
            elif in_str:
                cur_str = (cur_str or "") + chunk
        elif not line:
            if cur_id is not None:
                out[cur_id] = cur_str or ""
                cur_id = cur_str = None
                in_id = in_str = False
    if cur_id is not None:
        out[cur_id] = cur_str or ""
    return out


def _unquote(s: str) -> str:
    """Strip surrounding quotes + decode gettext escapes; UTF-8 stays as-is."""
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r",
                       '"': '"', "\\": "\\"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


PULL_DIALOG_MSGIDS = [
    "Pull Ollama model",   # title
    "Pull",                # OK button
]


# ── Static catalog tests ─────────────────────────────────────────────────


def test_pull_msgids_present_in_pot():
    """Catches catalog drift: source has _("Pull Ollama model")
    and _("Pull") wrapped via i18n, but if extract wasn't re-run
    the .pot won't carry them."""
    pot = _parse_po_msgs(POT)
    missing = [m for m in PULL_DIALOG_MSGIDS if m not in pot]
    assert not missing, (
        f"Pull-dialog msgids absent from {POT.name}: {missing}. "
        f"Run `./tools/i18n.sh extract` to refresh."
    )


def test_pull_msgstrs_translated_in_pl_po():
    """Pin: each Pull-dialog msgid must have a non-empty PL msgstr.
    Empty msgstr → gettext returns the msgid (English) at runtime,
    which is the user-reported bug."""
    po = _parse_po_msgs(PL_PO)
    untranslated = []
    for mid in PULL_DIALOG_MSGIDS:
        if mid not in po:
            untranslated.append(f"{mid!r}: ABSENT from catalog")
        elif not po[mid].strip():
            untranslated.append(f"{mid!r}: msgstr empty")
    assert not untranslated, (
        f"Pull dialog incomplete in {PL_PO.name}:\n  "
        + "\n  ".join(untranslated)
    )


def test_pull_pl_msgstrs_are_actually_polish():
    """Pin: msgstr must contain Polish stems / diacritics. A msgstr
    that equals the English msgid is a useless roundtrip."""
    po = _parse_po_msgs(PL_PO)
    bad = []
    pl_diacritics = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    for mid in PULL_DIALOG_MSGIDS:
        msgstr = po.get(mid, "").strip()
        if not msgstr:
            continue  # caught by previous test
        if msgstr == mid:
            bad.append(f"{mid!r} msgstr == msgid (lazy roundtrip)")
            continue
        # "Pull" → "Pobierz"; "Pull Ollama model" → "Pobierz model
        # Ollama". Both should contain "pobierz" or a Polish stem.
        ms_lower = msgstr.lower()
        looks_polish = (
            any(c in pl_diacritics for c in msgstr)
            or "pobierz" in ms_lower
            or "ściągn" in ms_lower
        )
        if not looks_polish:
            bad.append(f"{mid!r} msgstr {msgstr!r} doesn't look Polish")
    assert not bad, "\n  ".join(["non-Polish msgstrs:", *bad])


# ── Static source check: label must be translatable ─────────────────────


def test_label_uses_translation_function_not_bare_f_string():
    """Pin: the 'Model name (e.g. …)' label is currently an f-string
    that gettext can't see. Must be refactored to:

        lbl.set_markup(
            _("Model name (e.g. <tt>{primary}</tt>, <tt>{fallback}</tt>):"
            ).format(primary=rec_hint, fallback="llama3.1:8b")
        )

    so xgettext extracts the template and translators can place
    placeholders correctly in PL syntax."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    body_start = src.find("def _on_pull_model")
    body_end = src.find("\n    def ", body_start + 1)
    body = src[body_start:body_end]

    # Find the label set_markup site
    markup_match = re.search(
        r"lbl\.set_markup\(\s*([^)]+?)\)", body, re.DOTALL)
    assert markup_match, (
        "could not locate `lbl.set_markup(...)` in _on_pull_model. "
        "Test needs updating to track refactor."
    )
    markup_arg = markup_match.group(1).strip()
    # Must begin with `_(` — the gettext call wrapper
    assert markup_arg.startswith("_("), (
        f"`lbl.set_markup` argument is not wrapped in `_()` — "
        f"gettext can't translate this string. Got:\n"
        f"{markup_arg[:200]}\n"
        f"Refactor to: `_('Model name (e.g. <tt>{{primary}}</tt>, "
        f"<tt>{{fallback}}</tt>):').format(...)`"
    )


# ── Behavioural (xvfb) — dialog rendered in PL locale ────────────────────


_DRIVER = r'''
import json, os, sys
os.environ.setdefault("GTK_A11Y", "none")
os.environ.setdefault("NO_AT_BRIDGE", "1")
sys.path.insert(0, sys.argv[1])

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from bterminal.i18n import init_locale
init_locale("pl")

from bterminal import config as _cfg
_cfg._OPTIONS = {
    "theme": "dark", "font": "Monospace 11", "language": "pl",
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


parent_window = Gtk.Window()
parent_window.realize()
opts = OptionsDialog(parent_window)
opts.realize()
opts.show_all()
pump(50)

snapshot = {
    "title": None, "button_labels": [], "label_texts": [],
    "captured": False,
}

orig_run = Gtk.Dialog.run
def captured_run(self):
    snapshot["captured"] = True
    pump(50)
    snapshot["title"] = self.get_title()
    for btn in walk(self, Gtk.Button):
        snapshot["button_labels"].append(btn.get_label() or "")
    for lbl in walk(self, Gtk.Label):
        t = (lbl.get_text() or "").strip()
        if t:
            snapshot["label_texts"].append(t)
    return Gtk.ResponseType.CANCEL

Gtk.Dialog.run = captured_run
try:
    opts._on_pull_model()
finally:
    Gtk.Dialog.run = orig_run

print(json.dumps(snapshot, ensure_ascii=False))
'''


@pytest.fixture(scope="module")
def pl_dialog_snapshot(tmp_path_factory):
    if not _xvfb_available() and not os.environ.get("DISPLAY"):
        pytest.skip("needs xvfb-run or DISPLAY")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    driver_path = tmp_path_factory.mktemp("bug9") / "_driver.py"
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


def test_behavioural_dialog_title_is_polish(pl_dialog_snapshot):
    """In PL locale the dialog title should be Polish, not literal
    'Pull Ollama model'."""
    assert pl_dialog_snapshot["captured"], (
        f"didn't intercept dialog. {pl_dialog_snapshot}"
    )
    title = pl_dialog_snapshot["title"] or ""
    assert "Pull Ollama model" not in title, (
        f"dialog title is still English: {title!r}. Expected "
        f"'Pobierz model Ollama' or similar PL translation."
    )


def test_behavioural_pull_button_label_is_polish(pl_dialog_snapshot):
    """The OK button labelled 'Pull' in source must show 'Pobierz'
    (or another PL translation) at runtime in pl locale."""
    btn_labels = [l for l in pl_dialog_snapshot["button_labels"]
                  if l.strip()]
    has_english_pull = any(l == "Pull" for l in btn_labels)
    assert not has_english_pull, (
        f"button labelled 'Pull' (English) appears in PL-locale "
        f"dialog. All buttons: {btn_labels}"
    )


def test_behavioural_label_does_not_contain_english_model_name(
        pl_dialog_snapshot):
    """The 'Model name (e.g. …)' label must be in PL — most
    likely 'Nazwa modelu (np. …)'."""
    labels = pl_dialog_snapshot["label_texts"]
    bad = [l for l in labels if l.startswith("Model name")]
    assert not bad, (
        f"label still starts with English 'Model name…': {bad}. "
        f"Expected something like 'Nazwa modelu (np. …)'. "
        f"Note: this string is currently a bare f-string; static "
        f"test above pins the wrapper-fix."
    )
