"""Unit tests for plugin contracts: BTerminalPlugin ABC + Sidecar runtime."""

import json
import time

import pytest

from bterminal import plugin_runtime
from bterminal import sidecar_runtime
# ─── BTerminalPlugin (in-process contract) ────────────────────────────────────

def test_plugin_abc_required_methods_present():
    """The base class exposes all 5 lifecycle methods + class attrs that
    plugin authors override. If any of these change names, every external
    plugin (RemoteControll etc.) breaks — regression guard."""
    p = plugin_runtime.BTerminalPlugin()
    assert hasattr(p, "activate")
    assert hasattr(p, "deactivate")
    assert hasattr(p, "get_keyboard_shortcuts")
    assert hasattr(p, "on_sidebar_shown")
    assert hasattr(p, "get_session_context")
    # class attrs
    for attr in ("name", "title", "version", "description", "author", "default_in_session"):
        assert hasattr(p, attr), f"missing class attr {attr!r}"


def test_plugin_abc_default_methods_dont_crash():
    """Default no-op behavior — overriding is optional, plugin authors
    can use base methods until they need custom logic."""
    p = plugin_runtime.BTerminalPlugin()
    assert p.activate(app=None) is None
    p.deactivate()  # no-op
    assert p.get_keyboard_shortcuts() == []
    p.on_sidebar_shown()  # no-op
    assert p.get_session_context() is None


def test_plugin_subclass_overrides_take_effect():
    """Concrete plugin overrides base behavior — minimal contract sanity."""
    class FakePlugin(plugin_runtime.BTerminalPlugin):
        name = "fake"
        title = "Fake"
        default_in_session = False

        def get_session_context(self):
            return "fake context"

    p = FakePlugin()
    assert p.name == "fake"
    assert p.default_in_session is False
    assert p.get_session_context() == "fake context"


# ─── SidecarManifest ──────────────────────────────────────────────────────────

def test_manifest_required_field_only():
    """name is the only required field — partial manifests load with defaults."""
    m = sidecar_runtime.SidecarManifest(name="minimal")
    assert m.name == "minimal"
    assert m.plugin_address == ""
    assert m.run_command == ""
    assert m.default_in_session is True
    assert m.auto_start is False
    assert m.env == {}


def test_manifest_from_dict_drops_unknown_keys():
    """Future-proof manifests with extra fields don't break load_all."""
    data = {
        "name": "agent-tester",
        "plugin_address": "http://127.0.0.1:8081",
        "run_command": "python -m agent_tester.run",
        "future_feature_we_dont_know_yet": "ignored",
        "another_unknown": {"a": 1},
    }
    m = sidecar_runtime.SidecarManifest.from_dict(data)
    assert m.name == "agent-tester"
    assert m.run_command == "python -m agent_tester.run"


# ─── SidecarDiscovery ─────────────────────────────────────────────────────────

def test_discovery_loads_valid_manifests(tmp_path):
    (tmp_path / "btmsg.json").write_text(json.dumps({
        "name": "btmsg",
        "plugin_address": "http://127.0.0.1:8766",
        "run_command": "python -m btmsg",
    }))
    (tmp_path / "explorer.json").write_text(json.dumps({
        "name": "explorer",
        "run_command": "python -m explorer",
    }))
    d = sidecar_runtime.SidecarDiscovery(sidecars_dir=str(tmp_path))
    out = d.load_all()
    assert set(out.keys()) == {"btmsg", "explorer"}
    assert out["btmsg"].plugin_address == "http://127.0.0.1:8766"


def test_discovery_skips_invalid(tmp_path):
    """Garbage files / missing 'name' / non-JSON / dirs all silently skipped."""
    # valid one to anchor
    (tmp_path / "good.json").write_text(json.dumps({"name": "good"}))
    # malformed JSON
    (tmp_path / "broken.json").write_text("{not json")
    # JSON but missing 'name'
    (tmp_path / "no_name.json").write_text(json.dumps({"run_command": "x"}))
    # not a JSON extension — ignored entirely
    (tmp_path / "README.md").write_text("# notes")
    # nested dir — ignored
    (tmp_path / "subdir").mkdir()
    d = sidecar_runtime.SidecarDiscovery(sidecars_dir=str(tmp_path))
    out = d.load_all()
    assert set(out.keys()) == {"good"}


def test_discovery_missing_dir_returns_empty(tmp_path):
    d = sidecar_runtime.SidecarDiscovery(sidecars_dir=str(tmp_path / "does-not-exist"))
    assert d.load_all() == {}


# ─── SidecarRunner ────────────────────────────────────────────────────────────

def test_runner_start_idempotent(tmp_path):
    """Second start while running is a no-op — refcount is the caller's
    responsibility, runner only tracks the process."""
    runner = sidecar_runtime.SidecarRunner()
    manifest = sidecar_runtime.SidecarManifest(
        name="sleeper",
        run_command="sleep 30",
    )
    try:
        first = runner.start("sleeper", manifest)
        assert first["already_running"] is False
        assert isinstance(first["pid"], int)
        second = runner.start("sleeper", manifest)
        assert second["already_running"] is True
        assert second["pid"] == first["pid"]
        assert runner.is_running("sleeper") is True
    finally:
        runner.stop("sleeper")


def test_runner_stop_idempotent():
    """stop() on a never-started name is safe."""
    runner = sidecar_runtime.SidecarRunner()
    result = runner.stop("never-started")
    assert result == {"was_running": False}


def test_runner_empty_run_command_raises():
    runner = sidecar_runtime.SidecarRunner()
    manifest = sidecar_runtime.SidecarManifest(name="empty")
    with pytest.raises(RuntimeError, match="empty run_command"):
        runner.start("empty", manifest)


def test_runner_stop_all_clears_tracked_processes():
    """atexit hook — stop_all must clear out _procs dict."""
    runner = sidecar_runtime.SidecarRunner()
    m = sidecar_runtime.SidecarManifest(name="a", run_command="sleep 30")
    runner.start("a", m)
    assert len(runner._procs) == 1
    runner.stop_all()
    assert len(runner._procs) == 0


# ─── HealthChecker ────────────────────────────────────────────────────────────

def test_health_empty_url_returns_false():
    """No URL → no health — don't blindly hit blank URLs."""
    assert sidecar_runtime.HealthChecker.ping("") is False


def test_health_unreachable_returns_false():
    """Connection refused / timeout → False, but doesn't raise."""
    started = time.monotonic()
    result = sidecar_runtime.HealthChecker.ping(
        "http://127.0.0.1:1/never", timeout=0.5
    )
    assert result is False
    # Must respect timeout — not hang for default 2s
    assert time.monotonic() - started < 2.0
