"""Migration tests: claude_sessions.json → ai_sessions.json — T1.7 / R4b.

Covers the four scenarios in the implementation plan:
  1. No legacy file → no-op.
  2. Only legacy file → migrate, write ai_sessions.json, rename legacy → .bak.
  3. Both files exist → idempotent (don't touch ai_sessions.json).
  4. Legacy session with resume=true → after migration,
     provider_options.resume=true (top-level key removed).

Plus integration: AISessionManager() instantiation triggers migration.
"""
from __future__ import annotations

import json

import pytest

from bterminal import config, models
from bterminal.models import (
    AISessionManager,
    _migrate_claude_to_ai_sessions,
)


# ─── Direct migrator function ───────────────────────────────────────────────

def test_migration_no_legacy_file_is_noop(tmp_path):
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    assert _migrate_claude_to_ai_sessions(str(claude), str(ai)) is False
    assert not ai.exists()


def test_migration_creates_new_and_renames_legacy_to_bak(tmp_path, capsys):
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    claude.write_text(json.dumps([
        {"id": "a", "name": "alpha", "project_dir": "/tmp"},
        {"id": "b", "name": "beta", "project_dir": "/var"},
    ]))

    assert _migrate_claude_to_ai_sessions(str(claude), str(ai)) is True

    assert ai.exists()
    assert not claude.exists()
    assert (tmp_path / "claude_sessions.json.bak").exists()

    migrated = json.loads(ai.read_text())
    assert len(migrated) == 2
    assert all(s["provider"] == "claude" for s in migrated)
    # IDs / names preserved
    assert migrated[0]["id"] == "a"
    assert migrated[0]["name"] == "alpha"

    # Stderr log mentions the count
    captured = capsys.readouterr()
    assert "Migrated 2" in captured.err


def test_migration_idempotent_when_ai_already_exists(tmp_path):
    """Both files exist → don't overwrite ai_sessions.json."""
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    claude.write_text(json.dumps([{"id": "old", "name": "legacy"}]))
    ai.write_text(json.dumps([{"id": "new", "name": "current",
                               "provider": "claude"}]))

    assert _migrate_claude_to_ai_sessions(str(claude), str(ai)) is False
    # ai_sessions.json unchanged
    after = json.loads(ai.read_text())
    assert after[0]["id"] == "new"
    assert after[0]["name"] == "current"
    # Legacy file untouched (didn't get renamed to .bak)
    assert claude.exists()
    assert not (tmp_path / "claude_sessions.json.bak").exists()


def test_migration_wraps_legacy_flags_in_provider_options(tmp_path):
    """resume / skip_permissions / sudo / continue / model move from
    top-level into provider_options (R4.2 schema)."""
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    claude.write_text(json.dumps([{
        "id": "a", "name": "x", "project_dir": "/tmp",
        "resume": True, "skip_permissions": True, "sudo": False,
        "color": "#000000",
    }]))

    _migrate_claude_to_ai_sessions(str(claude), str(ai))
    s = json.loads(ai.read_text())[0]

    assert s["provider"] == "claude"
    assert s["provider_options"] == {
        "resume": True,
        "skip_permissions": True,
        "sudo": False,
    }
    # Non-provider keys stay top-level
    assert s["id"] == "a"
    assert s["name"] == "x"
    assert s["color"] == "#000000"
    # Legacy keys removed from top-level
    for k in ("resume", "skip_permissions", "sudo"):
        assert k not in s


def test_migration_merges_with_existing_provider_options(tmp_path):
    """If a session somehow already has provider_options + a top-level
    legacy flag, merge them (legacy keys win since they're being
    relocated, but pre-existing entries survive)."""
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    claude.write_text(json.dumps([{
        "id": "a", "name": "x",
        "provider_options": {"custom": "value"},
        "resume": True,
    }]))
    _migrate_claude_to_ai_sessions(str(claude), str(ai))
    s = json.loads(ai.read_text())[0]
    assert s["provider_options"] == {"custom": "value", "resume": True}


def test_migration_handles_corrupt_legacy_file(tmp_path, capsys):
    """Corrupt JSON in legacy file → don't crash, log warning, no migration."""
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    claude.write_text("{not valid json")

    assert _migrate_claude_to_ai_sessions(str(claude), str(ai)) is False
    assert not ai.exists()
    assert claude.exists()  # legacy file untouched
    captured = capsys.readouterr()
    assert "WARN" in captured.err


def test_migration_handles_non_list_legacy_file(tmp_path, capsys):
    """Legacy file is a JSON object (not list) → skip with warning."""
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    claude.write_text(json.dumps({"not": "a list"}))

    assert _migrate_claude_to_ai_sessions(str(claude), str(ai)) is False
    assert not ai.exists()
    captured = capsys.readouterr()
    assert "WARN" in captured.err


def test_migration_clobbers_stale_bak(tmp_path):
    """If a stale .bak exists from a prior failed migration, replace it."""
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    bak = tmp_path / "claude_sessions.json.bak"
    claude.write_text(json.dumps([{"id": "new", "name": "current"}]))
    bak.write_text(json.dumps([{"id": "stale", "name": "old"}]))

    _migrate_claude_to_ai_sessions(str(claude), str(ai))

    # New .bak is the just-migrated content (the freshly-renamed claude file)
    assert bak.exists()
    bak_content = json.loads(bak.read_text())
    assert bak_content[0]["id"] == "new"


def test_migration_double_call_is_safe(tmp_path):
    """Running migration twice is harmless: second call is no-op."""
    claude = tmp_path / "claude_sessions.json"
    ai = tmp_path / "ai_sessions.json"
    claude.write_text(json.dumps([{"id": "a", "name": "x"}]))

    first = _migrate_claude_to_ai_sessions(str(claude), str(ai))
    second = _migrate_claude_to_ai_sessions(str(claude), str(ai))

    assert first is True
    assert second is False
    # ai_sessions.json from first run intact
    assert json.loads(ai.read_text())[0]["id"] == "a"


# ─── Integration: AISessionManager() triggers migration ─────────────────────

def test_aisessionmanager_init_triggers_migration(tmp_path, monkeypatch):
    """Instantiating AISessionManager() with default filepath runs the
    migration as a side effect."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    claude_path = str(tmp_path / "claude_sessions.json")
    ai_path = str(tmp_path / "ai_sessions.json")
    monkeypatch.setattr(models, "CLAUDE_SESSIONS_FILE", claude_path)
    monkeypatch.setattr(models, "AI_SESSIONS_FILE", ai_path)

    # Seed legacy file
    with open(claude_path, "w") as f:
        json.dump([{"id": "a", "name": "x", "resume": True}], f)

    mgr = AISessionManager()  # no filepath → triggers migration

    # Migration ran
    assert (tmp_path / "ai_sessions.json").exists()
    assert (tmp_path / "claude_sessions.json.bak").exists()
    # Manager loaded the migrated content
    sessions = mgr.all()
    assert len(sessions) == 1
    assert sessions[0]["provider"] == "claude"
    assert sessions[0]["provider_options"]["resume"] is True


def test_aisessionmanager_init_skips_migration_when_explicit_filepath(tmp_path, monkeypatch):
    """When tests pass filepath=..., migration is skipped (no surprise
    side effects on a tmp_path filesystem)."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    claude_path = tmp_path / "claude_sessions.json"
    claude_path.write_text(json.dumps([{"id": "a", "name": "x"}]))
    monkeypatch.setattr(models, "CLAUDE_SESSIONS_FILE", str(claude_path))
    ai_path = str(tmp_path / "would-be-ai.json")
    monkeypatch.setattr(models, "AI_SESSIONS_FILE", ai_path)

    AISessionManager(filepath=str(tmp_path / "custom.json"))

    # Neither migration target nor .bak created
    assert not (tmp_path / "would-be-ai.json").exists()
    assert claude_path.exists()  # untouched
    assert not (tmp_path / "claude_sessions.json.bak").exists()
