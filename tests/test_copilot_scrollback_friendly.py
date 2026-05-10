"""Regression tests for task #68 — #66 revert.

Task #66 (2026-05-07) added a 'scrollback_friendly' checkbox + the
--screen-reader argv flag, hoping to disable Copilot's alt-screen
mode and let VTE scrollback capture output. TERM-matrix testing on
real copilot v1.0.43 (10 alternative TERM values) confirmed that
the alt-screen escape \\x1b[?1049h is hardcoded in the binary
regardless of TERM, --screen-reader, or any other documented flag.

The checkbox was removed because it advertised behavior the CLI
doesn't deliver. These tests pin the removal so a future contributor
who re-adds the toggle (without first solving the upstream alt-
screen issue) gets an immediate red light.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal.providers import load_providers_config, reset_registry
from bterminal.providers.copilot import CopilotProvider
from bterminal.ui.dialogs.ai_session import _PROVIDER_OPTION_KEYS


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


def test_scrollback_friendly_checkbox_removed_from_schema():
    """Schema must NOT expose a scrollback_friendly toggle — see
    docstring + README 'Known limitations' section. If you're
    re-adding it, first verify Copilot CLI actually disables alt-
    screen for some flag combo via the TERM-matrix probe in
    /tmp/copilot-*.log."""
    p = CopilotProvider(load_providers_config()["providers"]["copilot"])
    keys = [entry[0] for entry in p.get_dialog_schema()]
    assert "scrollback_friendly" not in keys, (
        f"task #68 expected scrollback_friendly removed; schema keys={keys}"
    )


def test_screen_reader_flag_not_emitted_unconditionally():
    """build_argv must not auto-add --screen-reader. The flag is a
    Copilot accessibility toggle (NVDA/JAWS); not a scroll fix."""
    import json
    import stat
    cfg = json.loads(
        json.dumps(load_providers_config()["providers"]["copilot"]))
    fake = Path("/tmp/copilot-fake-bin")
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    cfg["binary"]["search_paths"] = [str(fake)]
    p = CopilotProvider(cfg)

    # Default + with scrollback_friendly explicitly set should both
    # omit the flag (scrollback_friendly is no longer wired to argv).
    assert "--screen-reader" not in p.build_argv({}, "")
    assert "--screen-reader" not in p.build_argv(
        {"scrollback_friendly": True}, "")


def test_scrollback_friendly_not_in_provider_option_keys():
    """Removed from the dialog routing constant so save flow doesn't
    persist a non-functional flag in ai_sessions.json."""
    assert "scrollback_friendly" not in _PROVIDER_OPTION_KEYS
