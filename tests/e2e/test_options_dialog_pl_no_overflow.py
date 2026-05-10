"""E2E test for BUG#5 — Options dialog labels overflow horizontally
in Polish locale (visible cropping on left edge: 'ktualizacje przy
starcie' = 'Aktualizacje' with the leading 'A' chopped off).

User report (manual QA, 2026-05-10): with `language: "pl"` selected,
the Options dialog labels are wider than what the layout allocates
to the label column. Polish strings are typically 30-40% longer than
their English source ("Sprawdź aktualizacje przy starcie:" vs
"Check for updates at startup:") and the SizeGroup / column widths
were tuned to English minimums. Result: cropped text on the left
where labels are right-aligned (xalign=1), and overlap on the right
where checkboxes/values are left-aligned but extend past the dialog.

The test launches the actual OptionsDialog inside Xvfb with PL
locale, walks every Gtk.Label, translates each allocation to dialog
coordinates, and asserts:

  0 <= label.x   AND   label.x + label.width <= dialog.width

Anything that fails this constraint is bullet-proof overflow — the
label has been allocated space outside the dialog's visible area and
will be truncated by the WM/compositor.

A PNG snapshot of the PL-locale dialog is also saved for visual
review through the Read tool.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCREENSHOTS = REPO_ROOT / "smoke-logs" / "bug5-options-pl-overflow"


def _xvfb_available() -> bool:
    return shutil.which("xvfb-run") is not None


pytestmark = pytest.mark.skipif(
    not _xvfb_available() and not os.environ.get("DISPLAY"),
    reason="needs xvfb-run or a real DISPLAY",
)


_DRIVER = r'''
import json, os, sys
os.environ.setdefault("GTK_A11Y", "none")
os.environ.setdefault("NO_AT_BRIDGE", "1")
sys.path.insert(0, sys.argv[1])  # repo root

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import cairo

# Force PL before BTerminal imports — i18n module captures the
# language at install_translation() time.
from bterminal.i18n import init_locale, refresh_translatables
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


class _Dummy(Gtk.Window): pass


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


# Build dialog ─ realize parent first so coordinate translation works
parent = _Dummy()
parent.realize()
dlg = OptionsDialog(parent)
dlg.show_all()
# Realize ALL widgets (otherwise translate_coordinates returns None
# for never-allocated children).
for w in walk(dlg, Gtk.Widget):
    if not w.get_realized():
        w.realize()
pump(100)
refresh_translatables()  # belt-and-suspenders: force PL on existing tree
pump(100)

# Force a size by triggering a resize to a sane default. Without this,
# the dialog allocation may be 0×0 in headless Cairo.
dlg.set_default_size(680, 600)
dlg.resize(680, 600)
pump(100)

dlg_alloc = dlg.get_allocation()
result = {
    "dialog_size": [dlg_alloc.width, dlg_alloc.height],
    "labels": [],
    "overflows": [],
}

for lbl in walk(dlg, Gtk.Label):
    if not lbl.get_visible() or not lbl.get_realized():
        continue
    text = (lbl.get_text() or "").strip()
    if not text:
        continue
    al = lbl.get_allocation()
    # Translate the label's (0,0) corner to dialog coordinates.
    coords = lbl.translate_coordinates(dlg, 0, 0)
    if coords is None:
        continue
    x_in_dlg, y_in_dlg = coords
    info = {
        "text": text[:60],
        "x_in_dlg": x_in_dlg,
        "y_in_dlg": y_in_dlg,
        "width": al.width,
        "height": al.height,
        "right_edge": x_in_dlg + al.width,
    }
    result["labels"].append(info)

    # Overflow is: label allocated outside the dialog rectangle.
    # We allow a 1px slop because Cairo geometry sometimes rounds.
    if x_in_dlg < -1:
        result["overflows"].append({
            "kind": "left", **info,
        })
    if (x_in_dlg + al.width) > (dlg_alloc.width + 1):
        result["overflows"].append({
            "kind": "right", **info,
            "dlg_width": dlg_alloc.width,
        })

# Snapshot the dialog for visual review. Headless cairo + Xvfb
# sometimes leaves the dialog with a 0×0 allocation; in that case
# fall back to drawing onto a fixed canvas matching the requested
# default_size so we still get a visible PNG of the layout.
png_path = sys.argv[2]
os.makedirs(os.path.dirname(png_path), exist_ok=True)
draw_w = max(dlg_alloc.width, 680)
draw_h = max(dlg_alloc.height, 600)
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, draw_w, draw_h)
cr = cairo.Context(surf)
dlg.draw(cr)
surf.write_to_png(png_path)
result["screenshot"] = png_path
result["draw_size"] = [draw_w, draw_h]

print(json.dumps(result))
'''


@pytest.fixture(scope="module")
def pl_overflow_result(tmp_path_factory):
    """Run the GTK driver in PL locale under xvfb-run, parse JSON."""
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    driver_path = tmp_path_factory.mktemp("bug5_pl") / "_driver.py"
    driver_path.write_text(_DRIVER)
    png_path = SCREENSHOTS / "options_dialog_pl.png"
    cmd = [
        "xvfb-run", "-a",
        "--server-args=-screen 0 1920x1080x24",
        "python3", str(driver_path),
        str(REPO_ROOT), str(png_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        pytest.fail(
            f"driver crashed:\nrc={res.returncode}\n"
            f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    json_lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
    assert json_lines, (
        f"no JSON output from driver. stdout:\n{res.stdout}\n"
        f"stderr:\n{res.stderr}"
    )
    return json.loads(json_lines[-1])


# ── Assertions ───────────────────────────────────────────────────────────


def test_dialog_renders_with_at_least_some_labels(pl_overflow_result):
    """Sanity: PL OptionsDialog must show actual labels — if the
    driver fails to apply the translation or the dialog is empty,
    the rest of the test is meaningless."""
    labels = pl_overflow_result["labels"]
    assert len(labels) >= 5, (
        f"PL OptionsDialog produced only {len(labels)} labels — "
        f"likely an init failure. labels: {labels}"
    )


def test_no_label_overflows_left_edge_of_dialog(pl_overflow_result):
    """Pin: every label's x coordinate (translated to dialog space)
    must be ≥ 0. Anything negative means the label was allocated to
    the left of the dialog — its leftmost characters are clipped.

    User report screenshot (2026-05-10) showed 'ktualizacje przy
    starcie' with the leading 'A' chopped off — this is exactly
    that case."""
    left_overflows = [
        o for o in pl_overflow_result["overflows"]
        if o["kind"] == "left"
    ]
    assert not left_overflows, (
        f"{len(left_overflows)} labels overflow the LEFT edge of "
        f"the OptionsDialog in PL locale. Sample (first 3):\n"
        + "\n".join(
            f"  - {o['text']!r}: x={o['x_in_dlg']}px width={o['width']}px"
            for o in left_overflows[:3]
        )
    )


def test_no_label_overflows_right_edge_of_dialog(pl_overflow_result):
    """Pin: every label's right edge (x + width) must be ≤ dialog
    width. Polish strings are longer than English; if SizeGroup or
    column widths weren't recomputed, labels can extend past the
    dialog's right border."""
    right_overflows = [
        o for o in pl_overflow_result["overflows"]
        if o["kind"] == "right"
    ]
    assert not right_overflows, (
        f"{len(right_overflows)} labels overflow the RIGHT edge of "
        f"the OptionsDialog in PL locale. Dialog width: "
        f"{pl_overflow_result['dialog_size'][0]}px. Sample:\n"
        + "\n".join(
            f"  - {o['text']!r}: right_edge={o['right_edge']}px "
            f"vs dlg_width={o['dlg_width']}px"
            for o in right_overflows[:3]
        )
    )


def test_pl_snapshot_persisted_for_visual_review(pl_overflow_result):
    """The PNG produced by cairo widget.draw() must exist + be
    non-trivial. Visual review (Read tool) confirms the BUG#5
    pattern: clipped left edge labels, dialog content visibly
    too narrow for the longer Polish text."""
    assert pl_overflow_result["screenshot"], (
        "no screenshot path in driver output — dialog allocation "
        f"may be 0×0. Result: {pl_overflow_result}"
    )
    p = Path(pl_overflow_result["screenshot"])
    assert p.is_file(), f"missing snapshot: {p}"
    # Even a near-empty cairo render produces a few KB. A real,
    # populated dialog draws to 50KB+. We accept anything > 1500
    # bytes as proof the file was written by the driver, not as
    # a render-quality guarantee — visual review covers that.
    assert p.stat().st_size > 1500, (
        f"snapshot too small ({p.stat().st_size} bytes). Path: {p}"
    )
