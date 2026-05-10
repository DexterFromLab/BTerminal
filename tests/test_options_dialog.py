"""E2E regression tests for OptionsDialog (#154).

Covers tasks #151 (Ollama daemon Start/Stop UI), #152 (ScrolledWindow
+ screen-cap), #153 (collapse-shrink floor). Headless via Xvfb +
cairo widget.draw() — no ssh, no real X server, no xdotool. Each
test exercises a real GTK widget tree and asserts on:

  - widget hierarchy (correct nodes present after expander mutations)
  - geometry constraints (fits within 80% workarea, ≥480px floor)
  - ScrolledWindow scrollability (vadjustment.upper > page_size when
    content overflows)
  - screenshot dump for visual review (smoke-logs/options-e2e/)

Skips cleanly when DISPLAY unavailable + no Xvfb installed.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = REPO_ROOT / "smoke-logs" / "options-e2e"


def _xvfb_available() -> bool:
    return shutil.which("xvfb-run") is not None


pytestmark = pytest.mark.skipif(
    not _xvfb_available() and not os.environ.get("DISPLAY"),
    reason="needs xvfb-run or a real DISPLAY",
)


# ── Driver script that runs inside Xvfb subprocess ──────────────────────────
#
# We can't import Gtk at the top of this test module — pytest collection
# would fail on hosts without a display. Instead we shell out to a tiny
# Python driver under xvfb-run, communicate via JSON, and assert on it.

_DRIVER = r'''
import json, os, sys
os.environ.setdefault("GTK_A11Y", "none")
os.environ.setdefault("NO_AT_BRIDGE", "1")
sys.path.insert(0, sys.argv[1])  # repo root

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import cairo

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


class _Dummy(Gtk.Window): pass


def walk(w, cls):
    out = []
    if isinstance(w, cls): out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children(): out.extend(walk(c, cls))
    return out


def find_button_by_label(dlg, label):
    for b in walk(dlg, Gtk.Button):
        l = b.get_label() or ""
        if label.lower() in l.lower().replace("_", ""):
            return b
    return None


def snapshot(dlg, png_path):
    al = dlg.get_allocation()
    if al.width <= 0 or al.height <= 0:
        return False
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, al.width, al.height)
    cr = cairo.Context(surf)
    dlg.draw(cr)
    surf.write_to_png(png_path)
    return True


def pump(n=80):
    ctx = GLib.MainContext.default()
    for _ in range(n):
        while ctx.iteration(False): pass


# ── Build dialog ────────────────────────────────────────────────────────────
parent = _Dummy()
parent.realize()
dlg = OptionsDialog(parent)
dlg.show_all()
pump()

screenshots_dir = sys.argv[2]
os.makedirs(screenshots_dir, exist_ok=True)
result = {"steps": [], "ok": True, "errors": []}


def _check(cond, label):
    if not cond:
        result["ok"] = False
        result["errors"].append(label)


def step(name, mutate, screenshot_name):
    if mutate is not None:
        mutate()
        pump(150)
    info = {"name": name}
    sz = dlg.get_size()
    al = dlg.get_allocation()
    info["size"] = list(sz)
    info["alloc"] = [al.width, al.height]
    info["expanders_visible"] = len(walk(dlg, Gtk.Expander))
    info["save_visible"] = find_button_by_label(dlg, "Save") is not None
    info["cancel_visible"] = find_button_by_label(dlg, "Cancel") is not None
    sw = walk(dlg, Gtk.ScrolledWindow)
    if sw:
        first_sw = sw[0]
        vadj = first_sw.get_vadjustment()
        info["vadj_upper"] = vadj.get_upper()
        info["vadj_page"] = vadj.get_page_size()
        info["scrollable"] = vadj.get_upper() > vadj.get_page_size() + 1
        info["min_content_height"] = first_sw.get_min_content_height()
        info["propagate_natural_height"] = first_sw.get_propagate_natural_height()
    if screenshot_name:
        path = os.path.join(screenshots_dir, screenshot_name)
        info["screenshot"] = snapshot(dlg, path) and path
    result["steps"].append(info)


expanders = walk(dlg, Gtk.Expander)


# (a) baseline: collapsed state
step("baseline_collapsed", None, "01-baseline-collapsed.png")

# (b) expand AI Providers
step("expand_providers",
     lambda: expanders[0].set_expanded(True),
     "02-providers-expanded.png")

# (c) expand Local Models too (now both expanded)
step("expand_local_models",
     lambda: expanders[1].set_expanded(True),
     "03-both-expanded.png")

# (d) collapse AI Providers, Local Models still expanded
step("collapse_providers",
     lambda: expanders[0].set_expanded(False),
     "04-providers-collapsed-localmodels-still-up.png")

# (e) collapse both
step("collapse_both",
     lambda: [e.set_expanded(False) for e in expanders],
     "05-both-collapsed.png")

# Aggregate hierarchy info from final state
result["expanders_total"] = len(walk(dlg, Gtk.Expander))
result["scrolled_windows"] = len(walk(dlg, Gtk.ScrolledWindow))
result["min_height_floor_geom"] = dlg.get_preferred_height()[0]

# Screen workarea (for cap assertion)
display = Gdk.Display.get_default()
mon = display.get_primary_monitor() or display.get_monitor(0)
geom = mon.get_workarea() if mon else None
if geom:
    result["screen_workarea_h"] = geom.height
    result["cap_80pct"] = int(geom.height * 0.8)

print(json.dumps(result))
'''


@pytest.fixture(scope="module")
def driver_result(tmp_path_factory):
    """Run the GTK driver under xvfb-run, return parsed JSON result."""
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    driver_path = tmp_path_factory.mktemp("opts_e2e") / "_driver.py"
    driver_path.write_text(_DRIVER)
    cmd = ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24",
           "python3", str(driver_path), str(REPO_ROOT), str(SCREENSHOTS)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        pytest.fail(
            f"driver crashed:\nrc={res.returncode}\n"
            f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    # Last line of stdout is JSON
    lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
    assert lines, f"no JSON output:\n{res.stdout}\n{res.stderr}"
    import json
    return json.loads(lines[-1])


# ── Assertions ──────────────────────────────────────────────────────────────


def test_e2e_dialog_built_with_two_expanders_and_buttons(driver_result):
    """Hierarchy: AI Providers + Local Models expanders + Save+Cancel."""
    assert driver_result["expanders_total"] == 2
    assert driver_result["scrolled_windows"] >= 1
    baseline = driver_result["steps"][0]
    assert baseline["save_visible"]
    assert baseline["cancel_visible"]


def test_e2e_dialog_fits_within_80pct_screen_cap(driver_result):
    """#152: dialog never overflows monitor — every step's height ≤ 80%."""
    cap = driver_result.get("cap_80pct", 864)
    for step in driver_result["steps"]:
        h = step["size"][1]
        assert h <= cap + 20, (
            f"step {step['name']} height {h} > cap {cap}: {step}"
        )


def test_e2e_dialog_respects_min_height_floor(driver_result):
    """#153: dialog never shrinks below 480px floor (set_size_request)."""
    for step in driver_result["steps"]:
        h = step["size"][1]
        assert h >= 480, (
            f"step {step['name']} height {h} < 480 floor: {step}"
        )


def test_e2e_save_cancel_visible_in_every_step(driver_result):
    """#153: collapse cycle does not hide Save/Cancel buttons."""
    for step in driver_result["steps"]:
        assert step["save_visible"], f"Save missing in {step['name']}"
        assert step["cancel_visible"], f"Cancel missing in {step['name']}"


def test_e2e_both_expanders_present_after_full_collapse_cycle(driver_result):
    """#153: after expand→collapse cycle, both expanders still in tree."""
    final = driver_result["steps"][-1]  # collapse_both
    assert final["expanders_visible"] == 2, (
        f"Expanders disappeared after collapse: {final}"
    )


def test_e2e_local_models_visible_after_collapsing_providers(driver_result):
    """#153 specific: collapsing AI Providers does NOT hide Local Models."""
    step = next(s for s in driver_result["steps"]
                if s["name"] == "collapse_providers")
    assert step["expanders_visible"] == 2
    assert step["save_visible"] and step["cancel_visible"]


def test_e2e_scrolled_window_has_correct_policy(driver_result):
    """#152: ScrolledWindow non-propagating natural height + min ≥ 400."""
    baseline = driver_result["steps"][0]
    assert baseline["propagate_natural_height"] is False
    assert baseline["min_content_height"] >= 400


def test_e2e_scrollbar_appears_when_both_expanded(driver_result):
    """#152 acceptance: when both sections expanded, vadjustment shows
    overflow → vertical scrollbar would render in real X session."""
    step = next(s for s in driver_result["steps"]
                if s["name"] == "expand_local_models")
    assert step.get("scrollable", False), (
        f"both-expanded did not produce scrollable content: {step}"
    )


def test_e2e_screenshots_persisted_to_smoke_logs(driver_result):
    """Every step produced a non-empty PNG in smoke-logs/options-e2e/."""
    expected = [
        "01-baseline-collapsed.png",
        "02-providers-expanded.png",
        "03-both-expanded.png",
        "04-providers-collapsed-localmodels-still-up.png",
        "05-both-collapsed.png",
    ]
    for fname in expected:
        path = SCREENSHOTS / fname
        assert path.is_file(), f"missing {path}"
        assert path.stat().st_size > 1000, f"empty/too-small: {path}"
