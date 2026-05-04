"""Unit tests for models module — JsonListManager + SessionManager + ConsultManager."""

import json

import pytest

from bterminal import config
from bterminal import models
# ─── JsonListManager ─────────────────────────────────────────────────────────

def _new_jlm(tmp_path, monkeypatch, filename="items.json"):
    """Build a JsonListManager pointing at tmp_path/filename, with CONFIG_DIR
    monkey-patched so save() doesn't touch real ~/.config."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    return models.JsonListManager(str(tmp_path / filename))


def test_jlm_starts_empty_when_file_missing(tmp_path, monkeypatch):
    jlm = _new_jlm(tmp_path, monkeypatch)
    assert jlm.all() == []


def test_jlm_add_persists_and_assigns_id(tmp_path, monkeypatch):
    jlm = _new_jlm(tmp_path, monkeypatch)
    s = jlm.add({"name": "alpha"})
    assert "id" in s and len(s["id"]) > 0
    assert s["name"] == "alpha"
    # New manager picks up persisted state
    jlm2 = _new_jlm(tmp_path, monkeypatch)
    assert len(jlm2.all()) == 1
    assert jlm2.all()[0]["id"] == s["id"]


def test_jlm_update_changes_fields(tmp_path, monkeypatch):
    jlm = _new_jlm(tmp_path, monkeypatch)
    s = jlm.add({"name": "beta", "host": "old"})
    updated = jlm.update(s["id"], {"host": "new"})
    assert updated["host"] == "new"
    # Persists across reload
    jlm2 = _new_jlm(tmp_path, monkeypatch)
    assert jlm2.get(s["id"])["host"] == "new"


def test_jlm_delete_removes_entry(tmp_path, monkeypatch):
    jlm = _new_jlm(tmp_path, monkeypatch)
    s1 = jlm.add({"name": "a"})
    s2 = jlm.add({"name": "b"})
    jlm.delete(s1["id"])
    assert jlm.get(s1["id"]) is None
    assert jlm.get(s2["id"])["name"] == "b"


def test_jlm_corrupt_json_falls_back_to_empty(tmp_path, monkeypatch):
    """Damaged JSON file → start fresh, not crash."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    f = tmp_path / "items.json"
    f.write_text("{not really json")
    jlm = models.JsonListManager(str(f))
    assert jlm.all() == []


# ─── SessionManager ──────────────────────────────────────────────────────────

def test_session_manager_requires_host(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "SESSIONS_FILE", str(tmp_path / "sessions.json"))
    sm = models.SessionManager()
    with pytest.raises(ValueError, match="host"):
        sm.add({"name": "no-host"})
    # Valid session goes through
    sm.add({"name": "with-host", "host": "1.2.3.4"})
    assert len(sm.all()) == 1


# ─── ConsultManager ──────────────────────────────────────────────────────────

def _new_consult(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONSULT_CONFIG_FILE", str(tmp_path / "consult.json"))
    return models.ConsultManager()


def test_consult_writes_default_when_no_config(tmp_path, monkeypatch):
    cm = _new_consult(tmp_path, monkeypatch)
    assert cm.get_default_model() == models.ConsultManager.DEFAULT_CONFIG["default_model"]
    assert "google/gemini-2.5-pro" in cm.get_models()


def test_consult_set_default_model_persists(tmp_path, monkeypatch):
    cm = _new_consult(tmp_path, monkeypatch)
    cm.set_default_model("openai/gpt-4o")
    assert cm.get_default_model() == "openai/gpt-4o"
    # Reload with fresh manager
    cm2 = _new_consult(tmp_path, monkeypatch)
    assert cm2.get_default_model() == "openai/gpt-4o"


def test_consult_set_model_enabled_persists(tmp_path, monkeypatch):
    cm = _new_consult(tmp_path, monkeypatch)
    cm.set_model_enabled("anthropic/claude-sonnet-4", True)
    assert cm.get_models()["anthropic/claude-sonnet-4"]["enabled"] is True


def test_consult_remove_model_clears_default_if_was_it(tmp_path, monkeypatch):
    cm = _new_consult(tmp_path, monkeypatch)
    cm.set_default_model("openai/gpt-4o")
    cm.remove_model("openai/gpt-4o")
    assert cm.get_default_model() == ""
    assert "openai/gpt-4o" not in cm.get_models()


def test_consult_ensures_claude_code_models_on_load(tmp_path, monkeypatch):
    """Old config files without claude-code/* models get them auto-added."""
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(models, "CONFIG_DIR", str(tmp_path))
    f = tmp_path / "consult.json"
    monkeypatch.setattr(models, "CONSULT_CONFIG_FILE", str(f))
    legacy = {
        "api_key": "",
        "default_model": "google/gemini-2.5-pro",
        "models": {"google/gemini-2.5-pro": {"enabled": True, "name": "Gemini", "source": "openrouter"}},
    }
    f.write_text(json.dumps(legacy))
    cm = models.ConsultManager()
    # All claude-code/* should now exist and be enabled
    for mid in models.ConsultManager.CLAUDE_CODE_MODELS:
        assert mid in cm.get_models()
        assert cm.get_models()[mid]["enabled"] is True


def test_consult_project_preset_roundtrip(tmp_path, monkeypatch):
    cm = _new_consult(tmp_path, monkeypatch)
    preset = {"analyst": "claude-code/opus", "advocate": "openai/gpt-5-codex"}
    cm.save_project_preset("/some/project", preset)
    assert cm.get_project_preset("/some/project") == preset
    # Reload
    cm2 = _new_consult(tmp_path, monkeypatch)
    assert cm2.get_project_preset("/some/project") == preset
    cm2.delete_project_preset("/some/project")
    assert cm2.get_project_preset("/some/project") is None
