"""Unit tests for AISessionManager (rename z ClaudeSessionManager) — T1.6.

Verifies:
- AISessionManager exists and can be instantiated.
- New sessions automatically get provider="claude" via validate_entry.
- Explicit provider value (e.g. "copilot") is preserved.
- Legacy session files (without `provider` field) are tagged in-memory
  as provider="claude" on load() — file on disk stays unchanged
  (T1.7 owns the actual rename + .bak migration).
- Existing API (add/update/get/delete/all/save) unchanged.

T4.6.1 (2026-05-07): the `ClaudeSessionManager` legacy alias was
removed. Tests covering the alias identity / instantiation-via-old-name
were dropped along with the alias itself.
"""
from __future__ import annotations

import json

import pytest

from bterminal import config, models
from bterminal.models import AISessionManager


@pytest.fixture
def ai_manager(tmp_path, monkeypatch):
    """Build an AISessionManager pointing at tmp_path/sessions.json with
    CONFIG_DIR monkey-patched."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    return AISessionManager(filepath=str(tmp_path / "sessions.json"))


# ─── T4.6.1 alias removal sanity ────────────────────────────────────────────

def test_legacy_claude_session_manager_alias_removed():
    """After T4.6.1 the `ClaudeSessionManager` alias is gone. Pre-T4.6.1
    code that hadn't migrated to `AISessionManager` will trip on this
    and be forced to update. We deliberately avoid `importlib.reload`
    here — reload would mint fresh class objects and break identity
    asserts in tests that imported AISessionManager earlier."""
    assert not hasattr(models, "ClaudeSessionManager"), (
        "ClaudeSessionManager alias should be removed; found one"
    )


# ─── New-session provider auto-tagging ───────────────────────────────────────

def test_new_session_gets_provider_claude_by_default(ai_manager):
    s = ai_manager.add({"name": "alpha", "project_dir": "/tmp"})
    assert s["provider"] == "claude"


def test_explicit_provider_preserved(ai_manager):
    s = ai_manager.add({"name": "beta", "provider": "copilot"})
    assert s["provider"] == "copilot"


def test_provider_persisted_on_disk(ai_manager, tmp_path):
    ai_manager.add({"name": "gamma"})
    on_disk = json.loads((tmp_path / "sessions.json").read_text())
    assert on_disk[0]["provider"] == "claude"


# ─── Legacy session migration on load (in-memory only) ──────────────────────

def test_legacy_sessions_tagged_with_provider_on_load(tmp_path, monkeypatch):
    """Pre-T1.6 session file has no `provider` field. On load() each
    session gets provider="claude" in memory. The on-disk file stays
    unchanged until something triggers save() — T1.7 owns the actual
    rename + .bak migration."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text(json.dumps([
        {"id": "a", "name": "old1", "project_dir": "/tmp"},
        {"id": "b", "name": "old2", "project_dir": "/var"},
    ]))
    mgr = AISessionManager(filepath=str(sessions_file))
    all_sessions = mgr.all()
    assert all(s["provider"] == "claude" for s in all_sessions)
    # File on disk was NOT modified (no `provider` field yet)
    on_disk = json.loads(sessions_file.read_text())
    assert "provider" not in on_disk[0]
    assert "provider" not in on_disk[1]


def test_legacy_explicit_provider_preserved_on_load(tmp_path, monkeypatch):
    """If a session file already has `provider`, don't overwrite."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text(json.dumps([
        {"id": "a", "provider": "copilot", "name": "ai-tab"},
    ]))
    mgr = AISessionManager(filepath=str(sessions_file))
    assert mgr.all()[0]["provider"] == "copilot"


# ─── Pre-existing CRUD API still works ──────────────────────────────────────

def test_add_assigns_uuid_id(ai_manager):
    s = ai_manager.add({"name": "x"})
    assert "id" in s and len(s["id"]) > 0


def test_update_preserves_provider(ai_manager):
    s = ai_manager.add({"name": "x", "provider": "copilot"})
    updated = ai_manager.update(s["id"], {"name": "y"})
    assert updated["name"] == "y"
    assert updated["provider"] == "copilot"  # untouched


def test_delete_works(ai_manager):
    s = ai_manager.add({"name": "x"})
    ai_manager.delete(s["id"])
    assert ai_manager.get(s["id"]) is None


def test_all_returns_copy(ai_manager):
    ai_manager.add({"name": "a"})
    ai_manager.add({"name": "b"})
    listed = ai_manager.all()
    assert len(listed) == 2
    listed.append({"injected": True})
    assert len(ai_manager.all()) == 2  # internal state unaffected


# ─── Default filepath (T1.7: now ai_sessions.json) ─────────────────────────

def test_default_filepath_is_ai_sessions(tmp_path, monkeypatch):
    """T1.7: the canonical on-disk file is ai_sessions.json."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CLAUDE_SESSIONS_FILE",
                        str(tmp_path / "claude_sessions.json"))
    monkeypatch.setattr(models, "AI_SESSIONS_FILE",
                        str(tmp_path / "ai_sessions.json"))
    mgr = AISessionManager()
    mgr.add({"name": "x"})
    assert (tmp_path / "ai_sessions.json").exists()
    assert not (tmp_path / "claude_sessions.json").exists()
