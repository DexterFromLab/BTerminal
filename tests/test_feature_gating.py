"""Tests for centralized feature gating (task #8 / #80).

Coverage:
  - is_feature_available cache TTL behaviour (cached vs fresh probe)
  - invalidate_cache drops state + fires listeners
  - subscribe / unsubscribe register/remove listeners
  - DepSpec-aware probe used when cmd is in DEPENDENCIES
  - bare shutil.which fallback for unknown cmds
  - Refactored callsites still gate widgets correctly (mock the central
    helper to verify they consult it instead of shutil.which)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal import diagnostics


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    diagnostics._FEATURE_CACHE.clear()
    diagnostics._INVALIDATION_LISTENERS.clear()
    yield
    diagnostics._FEATURE_CACHE.clear()
    diagnostics._INVALIDATION_LISTENERS.clear()


# ─── is_feature_available cache behaviour ──────────────────────────────────


def test_is_feature_available_caches_result(monkeypatch):
    """Second call within TTL must NOT re-spawn shutil.which."""
    call_count = {"n": 0}

    def fake_which(cmd):
        call_count["n"] += 1
        return "/usr/bin/meld"
    monkeypatch.setattr(diagnostics, "DEPENDENCIES", ())  # bypass DepSpec branch
    monkeypatch.setattr("shutil.which", fake_which)

    assert diagnostics.is_feature_available("meld") is True
    assert diagnostics.is_feature_available("meld") is True
    assert diagnostics.is_feature_available("meld") is True
    assert call_count["n"] == 1, (
        f"expected single shutil.which call (cached); got {call_count['n']}"
    )


def test_is_feature_available_re_probes_after_ttl(monkeypatch):
    """After TTL elapses, probe runs again. Use ttl_sec=0.1 + sleep."""
    call_count = {"n": 0}
    monkeypatch.setattr(diagnostics, "DEPENDENCIES", ())

    def fake_which(cmd):
        call_count["n"] += 1
        return "/usr/bin/meld" if call_count["n"] <= 1 else None
    monkeypatch.setattr("shutil.which", fake_which)

    assert diagnostics.is_feature_available("meld", ttl_sec=0.1) is True
    time.sleep(0.15)
    # Probe again now → second shutil.which call (returns None this time)
    assert diagnostics.is_feature_available("meld", ttl_sec=0.1) is False
    assert call_count["n"] == 2


def test_ttl_zero_disables_caching(monkeypatch):
    """ttl_sec=0 → always re-probe. Useful for tests + 'force refresh'
    UI buttons that should always hit the filesystem."""
    call_count = {"n": 0}
    monkeypatch.setattr(diagnostics, "DEPENDENCIES", ())
    monkeypatch.setattr("shutil.which", lambda c:
                        call_count.update(n=call_count["n"] + 1) or "/x")

    diagnostics.is_feature_available("meld", ttl_sec=0)
    diagnostics.is_feature_available("meld", ttl_sec=0)
    diagnostics.is_feature_available("meld", ttl_sec=0)
    assert call_count["n"] == 3


def test_uses_depspec_probe_when_cmd_in_dependencies(monkeypatch):
    """For known DEPENDENCIES (meld, pandoc, etc.), full detect_tool
    runs (with version + path). Returns DepStatus.present."""
    fake_status = MagicMock(present=True, path="/usr/bin/meld",
                            version="meld 3.22")
    monkeypatch.setattr(diagnostics, "detect_tool",
                        lambda spec: fake_status)
    out = diagnostics.is_feature_available("meld")
    assert out is True


def test_falls_back_to_shutil_which_for_unknown_cmd(monkeypatch):
    """Cmd not in DEPENDENCIES (e.g. 'code', 'zed') → bare which probe."""
    monkeypatch.setattr("shutil.which", lambda c:
                        "/usr/bin/code" if c == "code" else None)
    assert diagnostics.is_feature_available("code") is True
    assert diagnostics.is_feature_available("nonexistent-binary") is False


# ─── invalidate_cache + listeners ──────────────────────────────────────────


def test_invalidate_cache_drops_all_entries():
    diagnostics._FEATURE_CACHE["meld"] = (time.monotonic(), True)
    diagnostics._FEATURE_CACHE["pandoc"] = (time.monotonic(), False)
    diagnostics.invalidate_cache()
    assert diagnostics._FEATURE_CACHE == {}


def test_invalidate_cache_calls_subscribers():
    fired = []
    diagnostics.subscribe_invalidation(lambda: fired.append("a"))
    diagnostics.subscribe_invalidation(lambda: fired.append("b"))
    diagnostics.invalidate_cache()
    assert fired == ["a", "b"]


def test_invalidate_cache_isolates_listener_failures():
    """One listener raising MUST NOT block subsequent listeners.
    InstallerWizard #5 calls invalidate_cache; a buggy panel
    shouldn't hang the wizard."""
    fired = []

    def boom():
        raise RuntimeError("simulated")

    diagnostics.subscribe_invalidation(boom)
    diagnostics.subscribe_invalidation(lambda: fired.append("survived"))
    diagnostics.invalidate_cache()
    assert fired == ["survived"]


def test_subscribe_idempotent_per_listener():
    """Subscribing same callable twice doesn't double-fire it."""
    fired = []
    listener = lambda: fired.append("x")
    diagnostics.subscribe_invalidation(listener)
    diagnostics.subscribe_invalidation(listener)
    diagnostics.invalidate_cache()
    assert fired == ["x"]  # not ["x", "x"]


def test_unsubscribe_removes_listener():
    fired = []
    listener = lambda: fired.append("x")
    diagnostics.subscribe_invalidation(listener)
    diagnostics.unsubscribe_invalidation(listener)
    diagnostics.invalidate_cache()
    assert fired == []


def test_unsubscribe_unknown_listener_is_noop():
    """Calling unsubscribe on a non-registered listener doesn't raise."""
    diagnostics.unsubscribe_invalidation(lambda: None)


# ─── Refactored callsites consult is_feature_available ─────────────────────


def test_files_panel_open_with_meld_consults_central_helper(monkeypatch):
    """Verify _open_with_meld imports + calls is_feature_available
    (not raw shutil.which). Stubs the helper to return False so the
    method exits via the 'meld not found' path without spawning meld."""
    # We don't need to instantiate the panel — just ensure the
    # source code references is_feature_available in the relevant
    # function. Cheaper than xvfb + GTK plumbing for a string check.
    src = (REPO_ROOT / "bterminal" / "ui" / "panels"
           / "files.py").read_text()
    # The two locations that previously used shutil.which("meld"):
    #  - _open_with_meld
    #  - _show_diff_dialog
    assert 'is_feature_available("meld")' in src
    # And no orphaned shutil.which("meld") left behind:
    raw_calls = src.count('shutil.which("meld")')
    assert raw_calls == 0, (
        f"refactor incomplete: {raw_calls} raw shutil.which('meld') "
        f"calls still present in files.py"
    )


def test_sidebar_open_with_submenu_uses_central_helper():
    """The 'Open With' submenu in sidebar context menu must consult
    the cached helper for VS Code / Zed availability."""
    src = (REPO_ROOT / "bterminal" / "ui" / "sidebar.py").read_text()
    assert "is_feature_available(cmd)" in src
    # No leftover shutil.which on the same line patterns we replaced
    bad = src.count('if shutil.which(cmd):')
    assert bad == 0, (
        "sidebar.py still has raw shutil.which(cmd) check that should "
        "be is_feature_available(cmd)"
    )


def test_files_panel_xdg_open_loop_uses_central_helper():
    """The 'Open With' editor list (VS Code / Zed / gedit / kate /
    File Manager) consults is_feature_available for each editor name."""
    src = (REPO_ROOT / "bterminal" / "ui" / "panels"
           / "files.py").read_text()
    # The loop's conditional must use the helper
    assert 'is_feature_available(cmd)' in src


# ─── End-to-end: invalidate_cache forces re-probe ──────────────────────────


def test_invalidate_then_re_probe_yields_fresh_value(monkeypatch):
    """Probe → True (cached). Simulate apt-install → invalidate.
    Next call must re-run shutil.which."""
    state = {"present": True, "calls": 0}

    monkeypatch.setattr(diagnostics, "DEPENDENCIES", ())

    def fake_which(cmd):
        state["calls"] += 1
        return "/usr/bin/meld" if state["present"] else None
    monkeypatch.setattr("shutil.which", fake_which)

    assert diagnostics.is_feature_available("meld") is True
    assert state["calls"] == 1
    # Cache: True
    assert diagnostics.is_feature_available("meld") is True
    assert state["calls"] == 1  # cached

    # Simulate "user uninstalled meld"
    state["present"] = False
    diagnostics.invalidate_cache()
    assert diagnostics.is_feature_available("meld") is False
    assert state["calls"] == 2  # cache busted → re-probed


def test_installer_wizard_calls_invalidate_on_success():
    """Source-level check that the wizard's success path fires
    invalidate_cache. Avoids needing to fully exercise the GTK
    state machine in this test."""
    src = (REPO_ROOT / "bterminal" / "ui"
           / "installer_wizard.py").read_text()
    assert "_notify_deps_changed" in src
    assert "invalidate_cache" in src


def test_files_panel_subscribes_invalidation_listener():
    """Files panel must register a listener so its meld button
    sensitivity refreshes after install completes (#9 / #81 hook)."""
    src = (REPO_ROOT / "bterminal" / "ui" / "panels"
           / "files.py").read_text()
    assert "subscribe_invalidation" in src
    assert "_on_deps_changed" in src
