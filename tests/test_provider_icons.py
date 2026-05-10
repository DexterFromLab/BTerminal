"""Tests for provider icon resolution + sidebar pixbuf rendering (task #57).

Three layers:
  1. Pure: ProviderDisplay.icon_path field present + defaults.json carries it.
  2. Pure: resolve_provider_icon_path() resolves/falls-back correctly.
  3. GTK (skip without DISPLAY): sidebar TreeStore actually receives a
     Pixbuf for AI session rows whose provider has a valid icon_path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.base import (
    ProviderDisplay,
    resolve_provider_icon_path,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── (a) ProviderDisplay carries icon_path; defaults.json populates it ───────


def test_provider_display_dataclass_has_icon_path_field():
    """Frozen dataclass exposes icon_path with a default of None so old
    test fixtures that build ProviderDisplay without it keep working."""
    d = ProviderDisplay(icon="X", short_label="X", long_label="X", color="#000")
    assert hasattr(d, "icon_path")
    assert d.icon_path is None


def test_defaults_json_provides_icon_path_for_both_providers():
    """Bundled defaults.json must populate icon_path so out-of-the-box
    installs render real logos, not just the emoji fallback."""
    reg = ProviderRegistry(config=load_providers_config())
    claude = reg.get("claude")
    copilot = reg.get("copilot")
    assert claude.display.icon_path == "icons/claude.svg"
    assert copilot.display.icon_path == "icons/copilot.svg"


# ─── (b) resolve_provider_icon_path: relative / absolute / missing ──────────


def test_resolve_returns_none_for_unset_path():
    assert resolve_provider_icon_path(None) is None
    assert resolve_provider_icon_path("") is None


def test_resolve_relative_path_lands_in_defaults_dir():
    """Relative path joins defaults/ and returns absolute string when
    the file exists. Bundled icons should resolve."""
    out = resolve_provider_icon_path("icons/claude.svg")
    assert out is not None
    assert Path(out).is_file()
    assert Path(out).is_absolute()
    assert out.endswith("/defaults/icons/claude.svg")


def test_resolve_relative_path_returns_none_when_file_missing():
    """Missing file → None so callers fall back to emoji rather than
    crashing on Pixbuf load."""
    assert resolve_provider_icon_path("icons/does-not-exist.svg") is None


def test_resolve_absolute_path_returns_as_is_when_exists(tmp_path):
    f = tmp_path / "x.svg"
    f.write_text("<svg/>")
    out = resolve_provider_icon_path(str(f))
    assert out == str(f)


def test_resolve_absolute_path_returns_none_when_missing(tmp_path):
    assert resolve_provider_icon_path(str(tmp_path / "missing.svg")) is None


# ─── (b/c) Sidebar pixbuf rendering — GTK integration ────────────────────────


if not os.environ.get("DISPLAY"):
    pytest.skip(
        "Sidebar pixbuf tests need a display; "
        "run with `xvfb-run -a pytest tests/test_provider_icons.py`",
        allow_module_level=True,
    )

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

from bterminal.ui.sidebar import _provider_pixbuf  # noqa: E402


def test_sidebar_provider_pixbuf_returns_real_pixbuf_for_claude():
    """Provider with a valid icon_path produces a Pixbuf, not None."""
    reg = ProviderRegistry(config=load_providers_config())
    claude = reg.get("claude")
    pix = _provider_pixbuf(claude, size=16)
    assert pix is not None
    assert isinstance(pix, GdkPixbuf.Pixbuf)
    assert pix.get_width() == 16
    assert pix.get_height() == 16


def test_sidebar_provider_pixbuf_returns_real_pixbuf_for_copilot():
    reg = ProviderRegistry(config=load_providers_config())
    copilot = reg.get("copilot")
    pix = _provider_pixbuf(copilot, size=16)
    assert pix is not None
    assert pix.get_width() == 16


def test_sidebar_provider_pixbuf_returns_none_when_path_missing():
    """Provider whose icon_path doesn't resolve falls back to None,
    letting the sidebar use the emoji column instead."""
    cfg = load_providers_config()
    # Copy + corrupt the icon_path so the file doesn't exist
    bad_cfg = {
        "default_provider": "claude",
        "providers": {
            "claude": {
                **cfg["providers"]["claude"],
                "display": {
                    **cfg["providers"]["claude"]["display"],
                    "icon_path": "icons/this-does-not-exist.svg",
                },
            },
        },
    }
    reg = ProviderRegistry(config=bad_cfg)
    pix = _provider_pixbuf(reg.get("claude"), size=16)
    assert pix is None


def test_sidebar_provider_pixbuf_returns_none_when_field_unset():
    """Old-style provider config without icon_path → emoji-only mode."""
    cfg = load_providers_config()
    cfg_no_path = {
        "default_provider": "claude",
        "providers": {
            "claude": {
                **cfg["providers"]["claude"],
                "display": {k: v for k, v in
                            cfg["providers"]["claude"]["display"].items()
                            if k != "icon_path"},
            },
        },
    }
    reg = ProviderRegistry(config=cfg_no_path)
    assert _provider_pixbuf(reg.get("claude"), size=16) is None


def test_sidebar_pixbuf_cache_returns_same_instance(monkeypatch):
    """Repeated calls for the same (path, size) hit the cache instead
    of re-decoding the SVG — important when refresh() rebuilds the
    tree on every session add/edit."""
    reg = ProviderRegistry(config=load_providers_config())
    p = reg.get("claude")
    pix1 = _provider_pixbuf(p, size=16)
    pix2 = _provider_pixbuf(p, size=16)
    assert pix1 is pix2  # exact same Pixbuf object


# ─── Sidebar TreeStore receives Pixbuf for AI session rows ──────────────────


from unittest.mock import MagicMock  # noqa: E402

from bterminal.ui.sidebar import (  # noqa: E402
    COL_ICON,
    COL_PIXBUF,
    SessionSidebar,
)


def _stub_app(ai_sessions=()):
    """Minimal app stub — sidebar reads .session_manager.all() (SSH)
    and .ai_manager.all() (AI). Both return lists."""
    app = MagicMock()
    app.session_manager.all.return_value = []
    app.ai_manager.all.return_value = list(ai_sessions)
    return app


def test_sidebar_ai_session_row_carries_pixbuf_when_provider_has_icon():
    """Bug regression: opening BT with a Claude session must populate
    COL_PIXBUF with a real Pixbuf and leave COL_ICON empty (otherwise
    both the SVG logo and the emoji render side-by-side)."""
    app = _stub_app(ai_sessions=[
        {"id": "a", "name": "MyClaude", "provider": "claude",
         "project_dir": "/tmp/x"},
    ])
    sidebar = SessionSidebar(app)

    # Walk the store: find the row with name="MyClaude"
    found = []

    def collect(model, path, it):
        if model.get_value(it, 1) == "MyClaude":  # COL_NAME
            found.append((
                model.get_value(it, COL_ICON),
                model.get_value(it, COL_PIXBUF),
            ))
    sidebar.store.foreach(collect)

    assert found, "Claude session row missing from sidebar tree"
    icon_text, pixbuf = found[0]
    assert pixbuf is not None, "AI session row must carry a Pixbuf"
    assert icon_text == "", (
        f"COL_ICON should be empty when COL_PIXBUF is set; got {icon_text!r}"
    )

    sidebar.destroy()


def test_sidebar_ai_session_falls_back_to_emoji_when_icon_path_missing(
    monkeypatch,
):
    """If we wipe out icon_path on the provider, the row should fall
    back to the emoji column with COL_PIXBUF=None — proves the
    fallback path works without SVG assets."""
    from bterminal.providers import get_registry as _real_registry

    # Build a registry with claude.icon_path stripped
    cfg = load_providers_config()
    cfg["providers"]["claude"]["display"].pop("icon_path", None)
    bad_reg = ProviderRegistry(config=cfg)

    monkeypatch.setattr(
        "bterminal.providers.get_registry", lambda: bad_reg,
    )
    # _provider_pixbuf imports get_registry indirectly via
    # resolve_provider_icon_path; clear cache to be safe
    from bterminal.ui.sidebar import _PIXBUF_CACHE
    _PIXBUF_CACHE.clear()

    app = _stub_app(ai_sessions=[
        {"id": "a", "name": "FallbackClaude", "provider": "claude",
         "project_dir": "/tmp/x"},
    ])
    sidebar = SessionSidebar(app)

    found = []

    def collect(model, path, it):
        if model.get_value(it, 1) == "FallbackClaude":
            found.append((
                model.get_value(it, COL_ICON),
                model.get_value(it, COL_PIXBUF),
            ))
    sidebar.store.foreach(collect)

    assert found, "fallback Claude session row missing"
    icon_text, pixbuf = found[0]
    assert pixbuf is None, "expected None pixbuf when icon_path missing"
    assert icon_text == "✨", (
        f"emoji fallback should render the provider's icon char; got {icon_text!r}"
    )

    sidebar.destroy()
