"""E2E test for BUG#7 — Options dialog vertical overflow without
visible scrollbar after expanding AI providers + Local Models.

User report (manual QA, 2026-05-10): "po rozwinięciu AI providers
+ Local Models zawartość nie mieści się w oknie. Trudno żeby się
mieściły, ale nie widać paska suwaka, to też nie poprawia
czytelności."

The existing test `tests/test_options_dialog.py::
test_e2e_scrollbar_appears_when_both_expanded` (#152) checks the
math: `vadjustment.upper > vadjustment.page_size`. That test passes
today in the regression suite — but the user STILL doesn't see a
scrollbar in the real VM. So the math-level assertion is necessary
but not sufficient.

This test adds the missing layer: the actual `Gtk.Scrollbar` widget
must be:
  1. Returned by `scrolled.get_vscrollbar()` (i.e. exists in tree)
  2. `get_visible()` true (visibility flag set)
  3. `get_realized()` true (widget actually rendered, not lazy)
  4. Allocated a non-zero width (real space in layout)

Plus a defensive check that the dialog itself doesn't grow taller
than the screen workarea (the alternative manifestation: the
ScrolledWindow shrinks to fit content because the dialog ate all
the height; nothing to scroll, but Save/Cancel are off-screen).

Driver runs under xvfb-run with screen 1920x944 (matches user's VM
resolution) so the geometry math reflects the real-world case.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCREENSHOTS = REPO_ROOT / "smoke-logs" / "bug7-options-scrollbar"


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
sys.path.insert(0, sys.argv[1])
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

# Apply locale BEFORE importing OptionsDialog so PL strings are
# baked into the widget hierarchy.
from bterminal.i18n import init_locale
init_locale(LANG)

from bterminal import config as _cfg
_cfg._OPTIONS = {
    "theme": "dark", "font": "Monospace 11", "language": LANG,
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


parent = _Dummy()
parent.realize()
dlg = OptionsDialog(parent)
dlg.show_all()
pump(100)

# Find the OUTER ScrolledWindow (the one wrapping the whole content
# at options.py:59). It's the first ScrolledWindow in the tree.
scrolls = walk(dlg, Gtk.ScrolledWindow)
outer = scrolls[0]


def snapshot_state(label):
    pump(60)
    al = dlg.get_allocation()
    sw_al = outer.get_allocation()
    vadj = outer.get_vadjustment()
    vbar = outer.get_vscrollbar()
    info = {
        "label": label,
        "dlg_size": [al.width, al.height],
        "scrolled_size": [sw_al.width, sw_al.height],
        "vadj_upper": vadj.get_upper(),
        "vadj_page": vadj.get_page_size(),
        "scrollable": vadj.get_upper() > vadj.get_page_size() + 1,
        "vbar_present": vbar is not None,
        "vbar_visible": bool(vbar and vbar.get_visible()),
        "vbar_realized": bool(vbar and vbar.get_realized()),
        "vbar_alloc": (
            [vbar.get_allocation().width, vbar.get_allocation().height]
            if vbar else None
        ),
        "min_content_height": outer.get_min_content_height(),
        "policy_v": outer.get_policy()[1].value_nick,
    }
    return info


# Capture: collapsed baseline
expanders = walk(dlg, Gtk.Expander)
states = []
states.append(snapshot_state("collapsed"))

# Expand AI Providers
expanders[0].set_expanded(True)
states.append(snapshot_state("ai_expanded"))

# Expand Local Models too
expanders[1].set_expanded(True)
states.append(snapshot_state("both_expanded"))

# Screen workarea — needed for "dialog must not exceed screen height"
display = Gdk.Display.get_default()
mon = display.get_primary_monitor() or display.get_monitor(0)
geom = mon.get_workarea() if mon else None
screen_h = geom.height if geom else None

print(json.dumps({"states": states, "screen_h": screen_h}))
'''


def _run_driver(tmp_path_factory, lang: str):
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    driver_path = (tmp_path_factory.mktemp(f"bug7_{lang}")
                   / "_driver.py")
    driver_path.write_text(_DRIVER)
    cmd = [
        "xvfb-run", "-a",
        "--server-args=-screen 0 1920x944x24",  # match user's VM
        "python3", str(driver_path), str(REPO_ROOT), lang,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        pytest.fail(
            f"driver crashed (lang={lang}):\nrc={res.returncode}\n"
            f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    json_lines = [l for l in res.stdout.splitlines() if l.startswith("{")]
    assert json_lines, (
        f"no JSON output (lang={lang}). stdout:\n{res.stdout}\n"
        f"stderr:\n{res.stderr}"
    )
    return json.loads(json_lines[-1])


@pytest.fixture(scope="module")
def scrollbar_result(tmp_path_factory):
    """English-locale baseline (existing #152 contract)."""
    return _run_driver(tmp_path_factory, "en")


@pytest.fixture(scope="module")
def scrollbar_result_pl(tmp_path_factory):
    """Polish locale — captures BUG#7 manifestation."""
    return _run_driver(tmp_path_factory, "pl")


def _both_expanded_state(result):
    for s in result["states"]:
        if s["label"] == "both_expanded":
            return s
    pytest.fail(f"no 'both_expanded' state in result: {result}")


# ── Assertions ───────────────────────────────────────────────────────────


def test_outer_scrolled_window_uses_automatic_vertical_policy(scrollbar_result):
    """Sanity: vertical policy must be AUTOMATIC. ALWAYS would
    waste space in collapsed state; NEVER would silently swallow
    overflow with no recourse."""
    state = _both_expanded_state(scrollbar_result)
    assert state["policy_v"] == "automatic", (
        f"expected vertical policy 'automatic' (value_nick), "
        f"got {state['policy_v']!r}"
    )


def test_vscrollbar_widget_is_present_after_both_expanded(scrollbar_result):
    """Pin: `Gtk.ScrolledWindow.get_vscrollbar()` returns the
    actual scrollbar widget. With AUTOMATIC policy, the widget
    exists from the start but visibility flips when needed."""
    state = _both_expanded_state(scrollbar_result)
    assert state["vbar_present"], (
        "outer ScrolledWindow.get_vscrollbar() returned None — "
        "the scrollbar isn't even in the widget tree, so it can "
        "never become visible"
    )


def test_vadjustment_indicates_overflow_after_both_expanded(scrollbar_result):
    """Math layer (existing #152 contract): when content is
    expanded, vadj.upper must exceed vadj.page_size — that's the
    GTK signal saying 'I can't show everything at once'."""
    state = _both_expanded_state(scrollbar_result)
    assert state["scrollable"], (
        f"both-expanded did not produce vadj.upper > page_size. "
        f"upper={state['vadj_upper']}, page={state['vadj_page']}. "
        f"Either content fits naturally (no scrollbar needed) or "
        f"the ScrolledWindow grew to fit content (defeating the "
        f"min-content-height cap)."
    )


def test_vscrollbar_is_visible_when_content_overflows(scrollbar_result):
    """Pin: this is the user-reported failure. With AUTOMATIC
    policy AND vadj.upper > page_size, GTK should set the
    scrollbar's visibility to True. If it doesn't, the user has
    no UI affordance to access the hidden content — exactly the
    BUG#7 manifestation."""
    state = _both_expanded_state(scrollbar_result)
    if not state["scrollable"]:
        pytest.skip("no overflow detected, scrollbar wouldn't show "
                    "anyway (covered by separate test)")
    assert state["vbar_visible"], (
        f"vadj overflows ({state['vadj_upper']} > "
        f"{state['vadj_page']}) but scrollbar widget visibility is "
        f"False. User cannot scroll. State: {state}"
    )
    assert state["vbar_realized"], (
        f"scrollbar visible=True but realized=False — the widget "
        f"is flagged for show but hasn't been allocated. State: "
        f"{state}"
    )


def test_dialog_height_does_not_exceed_screen(scrollbar_result):
    """Defensive: even with AUTOMATIC scrollbar, the dialog itself
    must not grow taller than the screen workarea (otherwise
    Save/Cancel buttons end up off-screen, which IS what the user
    described 'zawartość nie mieści się w oknie')."""
    state = _both_expanded_state(scrollbar_result)
    screen_h = scrollbar_result.get("screen_h")
    if not screen_h:
        pytest.skip("screen geometry not detected by driver")
    dlg_h = state["dlg_size"][1]
    # 80% cap is the existing #152 contract. Allow small slop for
    # WM border + chrome.
    cap = int(screen_h * 0.8) + 50
    assert dlg_h <= cap, (
        f"dialog height {dlg_h} > 80%+slop cap {cap} of screen "
        f"{screen_h}. Save/Cancel buttons may be off-screen and "
        f"the user can't dismiss the dialog cleanly. State: {state}"
    )


def test_collapsed_state_has_no_overflow(scrollbar_result):
    """Inverse sanity: in collapsed state the dialog should fit
    naturally without overflow. If vadj already shows overflow at
    baseline, the min-content-height is too small."""
    base = scrollbar_result["states"][0]
    assert base["label"] == "collapsed"
    # Allow 5px slop for any pre-render glitch
    assert (base["vadj_upper"] - base["vadj_page"]) < 5, (
        f"collapsed state already has overflow "
        f"(upper={base['vadj_upper']} page={base['vadj_page']}) — "
        f"min_content_height={base['min_content_height']} may be "
        f"too aggressive"
    )


# ── BUG#7-specific: PL locale where dialog grows to swallow scrollbar ──


def test_pl_locale_both_expanded_produces_visible_scrollbar(scrollbar_result_pl):
    """PIN BUG#7: in Polish locale with both expanders open the
    scrollbar must be VISIBLE with non-trivial allocation. Today
    (verified empirically on VM 2026-05-10) the dialog grows to
    fit content (vadj.upper == page_size), the scrollbar reports
    visible=True but allocation collapses to 1×1 pixel — user
    cannot see/use it.

    Real-VM Python introspection result on 2026-05-10:
      DLG_SIZE 560 720
      VADJ upper=670.0 page=670.0 scrollable=False
      VBAR visible=True realized=True alloc=1x1

    Acceptance: vbar allocation width must be ≥ 8px (a real
    scrollbar gutter)."""
    state = _both_expanded_state(scrollbar_result_pl)
    vbar_alloc = state.get("vbar_alloc")
    assert vbar_alloc is not None, (
        f"no vbar allocation captured. State: {state}"
    )
    vbar_w = vbar_alloc[0]
    assert vbar_w >= 8, (
        f"PL locale: vbar allocated {vbar_alloc} (width={vbar_w}px). "
        f"Threshold is ≥8px for a usable scrollbar gutter. "
        f"BUG#7 manifestation: dialog grew to fit content (vadj "
        f"upper={state['vadj_upper']}, page={state['vadj_page']}) "
        f"so the scrollbar collapses to a 1×1 stub. User reports "
        f"'nie widać paska suwaka' exactly because of this."
    )


def test_pl_locale_dialog_allows_overflow_so_scrollbar_can_show(
        scrollbar_result_pl):
    """PIN BUG#7 (alternative formulation): the dialog must not
    grow to swallow content. set_default_size + min_content_height
    must guarantee that vadj.upper > page_size when content is
    long (e.g. PL strings + both expanders).

    Today the dialog defaults to (560, min(720, screen_h*0.8)) =
    (560, 720). On any screen ≥ 900px tall this is enough for
    expanded content to fit naturally — scrollbar never triggers.

    Fix: cap default height harder (e.g. 480) so PL+expanded
    content always overflows and the scrollbar shows up."""
    state = _both_expanded_state(scrollbar_result_pl)
    overflow = state["vadj_upper"] - state["vadj_page"]
    assert overflow >= 30, (
        f"PL+both-expanded must produce real overflow (≥30px). "
        f"Got upper={state['vadj_upper']} page={state['vadj_page']} "
        f"overflow={overflow}px. Without overflow, scrollbar stays "
        f"collapsed regardless of policy=AUTOMATIC."
    )
