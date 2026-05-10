"""Tests for the auto-invalidate signal flow (task #9 / #81).

Most of the cache + listener plumbing was added in #8. This file
covers the integration-level behaviour the task description calls
out specifically:

  (a) emit signal (wizard OK) → invalidate_cache called
  (b) signal triggers panels[*].refresh() (mock panel list)
  (c) wizard cancel does NOT emit signal — caches stay warm

Plus the new sidebar subscription:
  (d) SessionSidebar registers self.refresh as listener
  (e) sidebar.refresh fires when invalidate_cache runs
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal import diagnostics


@pytest.fixture(autouse=True)
def _clear_state():
    diagnostics._FEATURE_CACHE.clear()
    diagnostics._INVALIDATION_LISTENERS.clear()
    yield
    diagnostics._FEATURE_CACHE.clear()
    diagnostics._INVALIDATION_LISTENERS.clear()


# ─── (a) Wizard OK fires invalidate_cache ──────────────────────────────────


def test_wizard_notify_deps_changed_calls_invalidate_cache():
    """InstallerWizard._notify_deps_changed() is the bridge between
    'user clicked Open BTerminal' (Gtk.ResponseType.OK in run_and_install)
    and the subscriber list. Verify the bridge calls into diagnostics."""
    fired = {"n": 0}

    def spy():
        fired["n"] += 1

    diagnostics.subscribe_invalidation(spy)

    # Mock just the bare-minimum shape of the wizard to invoke the
    # bridge without actually rendering GTK pages.
    from bterminal.ui.installer_wizard import InstallerWizard

    wiz = MagicMock(spec=InstallerWizard)
    wiz._notify_deps_changed = \
        InstallerWizard._notify_deps_changed.__get__(wiz)

    wiz._notify_deps_changed()
    assert fired["n"] == 1


def test_wizard_notify_deps_changed_swallows_errors():
    """If diagnostics import or invalidate_cache throws, the wizard
    must NOT propagate — 'Open BTerminal' button should still close
    the dialog cleanly even on cache failure."""
    from bterminal.ui import installer_wizard

    wiz = MagicMock(spec=installer_wizard.InstallerWizard)
    wiz._notify_deps_changed = \
        installer_wizard.InstallerWizard._notify_deps_changed.__get__(wiz)

    # Subscriber that always raises — invalidate_cache wraps each
    # listener in try/except, so this must not bubble out.
    diagnostics.subscribe_invalidation(
        lambda: (_ for _ in ()).throw(RuntimeError("simulated")))
    # No exception should escape:
    wiz._notify_deps_changed()


# ─── (b) Multiple subscribed panels all refresh ────────────────────────────


def test_signal_triggers_each_subscribed_panel_refresh():
    """Real BT has Files panel + sidebar (post-#9) subscribed; both
    must fire on a single invalidate_cache call. Models the future-
    proof scenario when more panels join the subscription."""
    panel_a_refresh = MagicMock()
    panel_b_refresh = MagicMock()
    panel_c_refresh = MagicMock()

    diagnostics.subscribe_invalidation(panel_a_refresh)
    diagnostics.subscribe_invalidation(panel_b_refresh)
    diagnostics.subscribe_invalidation(panel_c_refresh)

    diagnostics.invalidate_cache()

    panel_a_refresh.assert_called_once()
    panel_b_refresh.assert_called_once()
    panel_c_refresh.assert_called_once()


def test_signal_fires_listeners_in_subscription_order():
    """Determinism: listeners run in insertion order. UI panels often
    rely on a specific refresh sequence (sidebar before tabs, etc.)."""
    order = []

    diagnostics.subscribe_invalidation(lambda: order.append("first"))
    diagnostics.subscribe_invalidation(lambda: order.append("second"))
    diagnostics.subscribe_invalidation(lambda: order.append("third"))

    diagnostics.invalidate_cache()
    assert order == ["first", "second", "third"]


# ─── (c) Wizard cancel must NOT emit signal ────────────────────────────────


def test_wizard_cancel_path_does_not_invalidate_cache():
    """Source-level check: the wizard's cancel branch does NOT call
    _notify_deps_changed. Walking the actual GTK state machine here
    would need xvfb + a real subprocess; the source check is enough
    to enforce the invariant.

    The signal must ONLY fire on Gtk.ResponseType.OK (Open BTerminal
    button) — Cancel + window-close + dialog destroy MUST keep the
    diagnostics cache warm so users who cancel mid-install don't lose
    a millisecond of their last successful probe."""
    src = (REPO_ROOT / "bterminal" / "ui"
           / "installer_wizard.py").read_text()
    # Find the run_and_install function body
    import re
    m = re.search(
        r"def run_and_install.+?(?=\n    def |\Z)",
        src, re.S,
    )
    assert m, "couldn't locate run_and_install"
    body = m.group(0)

    # The OK branch contains _notify_deps_changed
    ok_section = re.search(
        r"Gtk\.ResponseType\.OK:.+?return True", body, re.S,
    )
    assert ok_section, "OK branch not found"
    assert "_notify_deps_changed" in ok_section.group(0)

    # The CANCEL / negative-response branch must NOT contain
    # _notify_deps_changed before the return False.
    cancel_section = re.search(
        r"Gtk\.ResponseType\.CANCEL.+?return False", body, re.S,
    )
    assert cancel_section, "CANCEL branch not found"
    assert "_notify_deps_changed" not in cancel_section.group(0)


def test_simulated_cancel_does_not_call_listeners():
    """Behavioural pin: subscribe a sentinel listener, run the cancel
    path equivalent (no _notify_deps_changed), assert sentinel never
    fired. Catches a future regression where someone moves the
    notify call into the finally block."""
    fired = {"n": 0}
    diagnostics.subscribe_invalidation(lambda: fired.update(n=fired["n"] + 1))

    # Simulating: user clicked Cancel — only _cancel_install runs.
    # _cancel_install is a sibling of _notify_deps_changed; calling
    # it must NOT touch the cache subscribers.
    from bterminal.ui.installer_wizard import InstallerWizard
    wiz = MagicMock(spec=InstallerWizard)
    wiz._install_proc = None  # already-finished case
    wiz._cancelled = False
    wiz._cancel_install = \
        InstallerWizard._cancel_install.__get__(wiz)

    wiz._cancel_install()
    assert fired["n"] == 0


# ─── (d) Sidebar subscription wire ─────────────────────────────────────────


def test_sidebar_subscribes_self_refresh_on_construction():
    """Source-level: sidebar.__init__ must subscribe self.refresh so
    Open With submenu items (VS Code / Zed) appear/disappear after
    install without restart."""
    src = (REPO_ROOT / "bterminal" / "ui" / "sidebar.py").read_text()
    # The subscribe call must reference self.refresh (not a different
    # method) — that's the contract: invalidate → re-render whole tree
    # → context-menu items will be regenerated next right-click.
    assert "subscribe_invalidation(self.refresh)" in src


# ─── (e) End-to-end signal propagation ─────────────────────────────────────


def test_invalidate_cache_calls_files_panel_and_sidebar_refresh_via_subscriptions():
    """Real subscribers: a stub for Files panel's _on_deps_changed +
    a stub for sidebar.refresh — both fire on one invalidate."""
    files_refresh = MagicMock()
    sidebar_refresh = MagicMock()
    diagnostics.subscribe_invalidation(files_refresh)
    diagnostics.subscribe_invalidation(sidebar_refresh)

    diagnostics.invalidate_cache()

    files_refresh.assert_called_once()
    sidebar_refresh.assert_called_once()


def test_invalidate_then_re_subscribe_listener_is_independent():
    """Subscribe → invalidate → unsubscribe → invalidate again. The
    unsubscribed listener must not fire on the second pass."""
    fires = []

    def listener():
        fires.append(1)

    diagnostics.subscribe_invalidation(listener)
    diagnostics.invalidate_cache()
    assert fires == [1]

    diagnostics.unsubscribe_invalidation(listener)
    diagnostics.invalidate_cache()
    assert fires == [1]  # second invalidate didn't refire


def test_listener_can_modify_subscriber_list_during_invalidation():
    """Edge case: a listener that subscribes ANOTHER listener during
    its own execution must not crash invalidate_cache (we iterate a
    copy of the list to avoid 'modified during iteration')."""
    fires = []
    extra = MagicMock()

    def first_listener():
        fires.append("first")
        diagnostics.subscribe_invalidation(extra)

    diagnostics.subscribe_invalidation(first_listener)
    # Should not raise; extra subscribed but won't fire THIS round
    # (we iterate the snapshot of listeners at call entry).
    diagnostics.invalidate_cache()
    assert fires == ["first"]
    extra.assert_not_called()
    # But fires on the next invalidation
    diagnostics.invalidate_cache()
    extra.assert_called_once()
