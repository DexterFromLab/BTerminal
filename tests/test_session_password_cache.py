"""Tests for R3a — passwords in-memory only.

Scope:
  R3a.1 — SessionManager.save() filters 'password'
  R3a.2 — SessionManager.load() drops 'password' from legacy data
  R3a.3 — SessionPasswordCache CRUD (set/get/pop/clear)
  R3a.6 — Migration log on legacy passwords
"""

import json
import sys

import pytest

from bterminal import config, models


# ─── SessionPasswordCache ────────────────────────────────────────────────────

def test_cache_set_and_get():
    cache = models.SessionPasswordCache()
    cache.set("sess-1", "secret123")
    assert cache.get("sess-1") == "secret123"


def test_cache_pop_returns_and_removes():
    cache = models.SessionPasswordCache()
    cache.set("sess-1", "secret")
    assert cache.pop("sess-1") == "secret"
    assert cache.get("sess-1") is None
    assert "sess-1" not in cache


def test_cache_get_missing_returns_none():
    cache = models.SessionPasswordCache()
    assert cache.get("never-set") is None
    assert cache.pop("never-set") is None


def test_cache_set_empty_drops_entry():
    """Empty string = "no password" — drop existing entry."""
    cache = models.SessionPasswordCache()
    cache.set("sess-1", "secret")
    cache.set("sess-1", "")
    assert cache.get("sess-1") is None


def test_cache_clear_removes_all():
    cache = models.SessionPasswordCache()
    cache.set("a", "1")
    cache.set("b", "2")
    cache.set("c", "3")
    assert len(cache) == 3
    cache.clear()
    assert len(cache) == 0


def test_cache_membership():
    cache = models.SessionPasswordCache()
    cache.set("yes", "x")
    assert "yes" in cache
    assert "no" not in cache


# ─── SessionManager save filters password ────────────────────────────────────

def test_session_manager_save_drops_password(tmp_path, monkeypatch):
    """R3a.1: save() filtruje password przed zapisem."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    f = tmp_path / "sessions.json"
    monkeypatch.setattr(models, "SESSIONS_FILE", str(f))

    sm = models.SessionManager()
    sm.add({"name": "test", "host": "1.2.3.4", "password": "should_not_persist"})

    # In-memory: password dropped
    assert sm.all()[0].get("password") is None
    # On disk: password not present
    on_disk = json.loads(f.read_text())
    assert on_disk[0].get("password") is None
    assert "password" not in on_disk[0]


def test_session_manager_load_strips_legacy_password(tmp_path, monkeypatch, capsys):
    """R3a.2 + R3a.6: load wykrywa legacy 'password', dropuje, loguje."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    f = tmp_path / "sessions.json"
    monkeypatch.setattr(models, "SESSIONS_FILE", str(f))

    # Legacy data: pre-R3a sessions.json with password
    legacy = [
        {"id": "1", "name": "old", "host": "h1", "password": "leaked123"},
        {"id": "2", "name": "newer", "host": "h2"},  # no password
    ]
    f.write_text(json.dumps(legacy))

    sm = models.SessionManager()
    captured = capsys.readouterr()

    # All sessions present
    assert len(sm.all()) == 2
    # No password in any in-memory session
    for s in sm.all():
        assert "password" not in s
    # Migration log to stderr
    assert "Migrated" in captured.err
    assert "1 session" in captured.err

    # File rewritten without passwords
    on_disk = json.loads(f.read_text())
    assert all("password" not in s for s in on_disk)


def test_session_manager_load_no_legacy_no_log(tmp_path, monkeypatch, capsys):
    """Brak legacy passwords → brak logu (silent)."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    f = tmp_path / "sessions.json"
    monkeypatch.setattr(models, "SESSIONS_FILE", str(f))

    f.write_text(json.dumps([{"id": "1", "name": "x", "host": "h"}]))
    sm = models.SessionManager()
    captured = capsys.readouterr()

    assert len(sm.all()) == 1
    assert captured.err == ""  # no migration message


def test_session_manager_update_does_not_persist_password(tmp_path, monkeypatch):
    """Even if caller passes password to update(), it gets filtered on save."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    f = tmp_path / "sessions.json"
    monkeypatch.setattr(models, "SESSIONS_FILE", str(f))

    sm = models.SessionManager()
    s = sm.add({"name": "test", "host": "1.1.1.1"})
    sm.update(s["id"], {"password": "ephemeral"})

    on_disk = json.loads(f.read_text())
    assert "password" not in on_disk[0]
