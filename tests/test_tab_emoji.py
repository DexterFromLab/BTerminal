"""Tests for the random per-tab emoji disambiguator (task #67).

Pure-helper coverage of the picker logic. GTK widget integration
(emoji renders as Gtk.Label on the right side of the tab label box)
is verified by the manual smoke test on VM.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_tab_emoji_pool_has_at_least_30_unique_glyphs():
    """Pool restored to its pre-T2.7 size — gives ≥30 simultaneous tabs
    a unique disambiguator before falling back to repeats."""
    from bterminal.app import BTerminalApp
    pool = BTerminalApp._TAB_EMOJIS
    assert isinstance(pool, list)
    assert len(pool) >= 30
    assert len(set(pool)) == len(pool), (
        f"emoji pool must be unique; got {len(pool)} entries, "
        f"{len(set(pool))} unique"
    )


def test_tab_emoji_pool_contains_original_set():
    """Sanity that the restored pool keeps the historical favorites
    (just so a long-running user doesn't notice their familiar
    glyphs vanished)."""
    from bterminal.app import BTerminalApp
    pool = set(BTerminalApp._TAB_EMOJIS)
    for staple in ("🦊", "🐙", "🚀", "⚡", "🔮", "🎲"):
        assert staple in pool, f"{staple} missing from restored pool"


def _stub_app_with_pages(used_emojis):
    """Construct a thin app stub with a fake notebook whose pages
    expose `_tab_emoji` like real TerminalTabs do. _pick_tab_emoji
    walks the notebook to detect collisions, so we just need that
    iteration to work."""
    from bterminal.app import BTerminalApp
    app = MagicMock(spec=BTerminalApp)
    pages = [MagicMock(_tab_emoji=e) for e in used_emojis]
    app.notebook = MagicMock()
    app.notebook.get_n_pages.return_value = len(pages)
    app.notebook.get_nth_page.side_effect = lambda i: pages[i]
    # Bind the real method to the stub so the test exercises the
    # actual selection logic rather than MagicMock's auto-magic.
    app._pick_tab_emoji = BTerminalApp._pick_tab_emoji.__get__(app)
    app._TAB_EMOJIS = BTerminalApp._TAB_EMOJIS
    return app


def test_pick_tab_emoji_avoids_used_when_possible():
    """No used emojis → picker may return any pool entry."""
    app = _stub_app_with_pages([])
    chosen = app._pick_tab_emoji()
    assert chosen in app._TAB_EMOJIS


def test_pick_tab_emoji_skips_in_use_glyphs():
    """3 tabs already use the first 3 pool entries → picker MUST
    return one of the remaining 27."""
    used = ["🦊", "🐙", "🎯"]
    app = _stub_app_with_pages(used)
    # Run multiple picks — none should ever return a used emoji
    for _ in range(50):
        chosen = app._pick_tab_emoji()
        assert chosen not in used, (
            f"picker returned in-use emoji {chosen!r}; available "
            f"pool minus used = "
            f"{sorted(set(app._TAB_EMOJIS) - set(used))}"
        )


def test_pick_tab_emoji_falls_back_to_full_pool_when_all_used():
    """≥30 tabs open → all pool entries used → picker can't avoid
    collision, returns from full pool (better than crashing)."""
    used = list(BTerminalApp_pool := __import__(
        "bterminal.app", fromlist=["BTerminalApp"]).BTerminalApp._TAB_EMOJIS)
    app = _stub_app_with_pages(used)
    chosen = app._pick_tab_emoji()
    assert chosen in app._TAB_EMOJIS  # any from the full pool is fine


def test_pick_tab_emoji_handles_pages_without_tab_emoji_attr():
    """Wizard / non-TerminalTab pages (no _tab_emoji attr) shouldn't
    confuse the picker. getattr default is None → ignored."""
    from bterminal.app import BTerminalApp
    app = MagicMock(spec=BTerminalApp)
    page_with = MagicMock(_tab_emoji="🦊")
    page_without = MagicMock(spec=[])  # no _tab_emoji attribute
    app.notebook = MagicMock()
    app.notebook.get_n_pages.return_value = 2
    app.notebook.get_nth_page.side_effect = lambda i: \
        page_with if i == 0 else page_without
    app._pick_tab_emoji = BTerminalApp._pick_tab_emoji.__get__(app)
    app._TAB_EMOJIS = BTerminalApp._TAB_EMOJIS

    # Doesn't crash, doesn't pick 🦊 (the only used)
    chosen = app._pick_tab_emoji()
    assert chosen != "🦊"


def test_pick_tab_emoji_returns_string_not_widget():
    """Sanity: picker returns a glyph (str), not a wrapped Gtk widget.
    _build_tab_label is what wraps it in Gtk.Label."""
    app = _stub_app_with_pages([])
    chosen = app._pick_tab_emoji()
    assert isinstance(chosen, str)
    assert len(chosen) > 0
