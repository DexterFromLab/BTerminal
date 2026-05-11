"""Pin tests for BUG#22 — aider wizard integration in BT.

The end-to-end UX is documented in smoke-logs/bug22-fix/ as a PNG
sequence (placeholder → wizard tab → pull → auto-relaunched aider).
These tests cover the *logic* glue that makes that sequence work:
sentinel parsing + relaunch-config decision. The GTK / VTE spawn parts
are visually verified in the smoke-logs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from bterminal.providers import aider_probe


# ─── Sentinel parsing ─────────────────────────────────────────────────────


def test_read_sentinel_missing_returns_none(tmp_path):
    """Pin: no sentinel file → None. App side then skips relaunch and
    leaves the user in BT with no AI tab — same as 'Anuluj' on BUG#19."""
    assert aider_probe.read_sentinel(str(tmp_path / "no-such.json")) is None


def test_read_sentinel_malformed_returns_none(tmp_path):
    """Pin: corrupt JSON / partial write race → None, never raise. The
    wizard child-exited callback runs in GLib.idle_add; an exception
    here would silently log to stderr and leave a stale sentinel."""
    bad = tmp_path / "sentinel.json"
    bad.write_text("{not valid")
    assert aider_probe.read_sentinel(str(bad)) is None


def test_read_sentinel_happy_path(tmp_path):
    sent = tmp_path / "s.json"
    sent.write_text(json.dumps({
        "session_id": "abc",
        "model": "openai/qwen2.5-coder:7b",
        "ts": 1234,
    }))
    got = aider_probe.read_sentinel(str(sent))
    assert got == {
        "session_id": "abc",
        "model": "openai/qwen2.5-coder:7b",
        "ts": 1234,
    }


# ─── Relaunch decision ────────────────────────────────────────────────────


def test_compute_relaunch_none_when_sentinel_missing():
    """Pin: payload=None (e.g. user closed wizard before saving) → None.
    Without this short-circuit App._on_aider_wizard_done would build a
    config with `model=None` and spawn a broken aider."""
    cfg = {"id": "sess-1", "provider": "aider"}
    assert aider_probe.compute_relaunch_config(None, cfg) is None


def test_compute_relaunch_none_when_session_id_mismatch():
    """Pin: stale sentinel from a previous wizard run (different session
    id) must NOT trigger a relaunch — that would hijack the wrong tab.
    Common case: user opens two aider sessions in sequence; the older
    sentinel can survive in /tmp."""
    payload = {"session_id": "OLD", "model": "openai/qwen2.5-coder:7b"}
    cfg = {"id": "NEW", "provider": "aider"}
    assert aider_probe.compute_relaunch_config(payload, cfg) is None


def test_compute_relaunch_none_when_model_field_empty():
    """Pin: defensive — wizard wrote sentinel but with empty model
    (cancelled mid-flow). No relaunch, no broken spawn."""
    payload = {"session_id": "X", "model": ""}
    cfg = {"id": "X", "provider": "aider"}
    assert aider_probe.compute_relaunch_config(payload, cfg) is None


def test_compute_relaunch_injects_model_into_provider_options():
    """Pin: happy path — sentinel matches, model is non-empty. Result
    config carries provider_options.model and preserves the rest of
    the session config (project_dir, prompt, enabled_plugins…)."""
    payload = {
        "session_id": "sess-42",
        "model": "openai/qwen2.5-coder:3b",
        "ts": 999,
    }
    cfg = {
        "id": "sess-42",
        "name": "testowanie_aidera",
        "provider": "aider",
        "project_dir": "/tmp/foo",
        "provider_options": {"resume": True, "model": "ignored-old"},
    }
    new_cfg = aider_probe.compute_relaunch_config(payload, cfg)
    assert new_cfg is not None
    assert new_cfg["provider_options"]["model"] == "openai/qwen2.5-coder:3b"
    # Other opts preserved (resume must survive the relaunch).
    assert new_cfg["provider_options"]["resume"] is True
    # Top-level fields preserved.
    assert new_cfg["name"] == "testowanie_aidera"
    assert new_cfg["project_dir"] == "/tmp/foo"
    # Defensive copy: original untouched.
    assert cfg["provider_options"]["model"] == "ignored-old"


def test_compute_relaunch_handles_config_without_provider_options():
    """Pin: legacy aider session entries (no provider_options block) —
    helper must still build a working config without KeyError."""
    payload = {"session_id": "X", "model": "openai/whatever:1b"}
    cfg = {"id": "X", "provider": "aider"}  # NB: no provider_options
    new_cfg = aider_probe.compute_relaunch_config(payload, cfg)
    assert new_cfg["provider_options"] == {"model": "openai/whatever:1b"}


# ─── End-to-end: wizard CLI subprocess writes sentinel that the
#     compute_relaunch_config helper accepts. ────────────────────────────


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WIZARD = REPO_ROOT / "tools" / "aider_setup_wizard"


def test_wizard_sentinel_round_trips_through_compute_relaunch(tmp_path):
    """Pin: invariant check across the BUG#21↔BUG#22 boundary.

    The wizard writes the sentinel; BT reads it through
    aider_probe.read_sentinel + compute_relaunch_config. If either side
    drifts (model field renamed, prefix dropped, session_id mismatch),
    the auto-relaunch is silently broken — user reports 'wizard worked
    but aider didn't come back'. This test exercises both sides in one
    subprocess invocation.
    """
    import stat, subprocess, sys

    # Fake `ollama` so wizard --headless doesn't try real network.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ollama"
    fake.write_text(
        "#!/bin/bash\n"
        "case \"$1\" in\n"
        "  list) echo 'NAME ID SIZE MODIFIED'; "
        "         echo 'qwen2.5-coder:7b deadbeef 4 GB 1 hour ago';;\n"
        "  pull) exit 0;;\n"
        "esac\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"

    rc = subprocess.run(
        [sys.executable, str(WIZARD), "--headless", "--no-pull",
         "--session-id", "round-trip-id"],
        env=env, capture_output=True, text=True, timeout=30,
    ).returncode
    assert rc == 0

    sentinel_file = (tmp_path / ".config" / "bterminal"
                     / ".aider_wizard_done.json")
    payload = aider_probe.read_sentinel(str(sentinel_file))
    assert payload is not None
    assert payload["session_id"] == "round-trip-id"

    new_cfg = aider_probe.compute_relaunch_config(
        payload, {"id": "round-trip-id", "provider": "aider"})
    assert new_cfg is not None
    assert new_cfg["provider_options"]["model"].startswith("openai/")
