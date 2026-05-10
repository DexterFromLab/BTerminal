"""Unit tests for bterminal.providers config loader — T1.2.

Covers:
- Bundled defaults.json structure (claude + copilot present)
- Defaults Claude capabilities match current behavior (intro_prompt,
  session_log, usage_api, rules_inject, task_auto_trigger, stats_bar)
- Copilot capabilities mostly disabled at T1.2 baseline (enabled in
  later tasks T2.3 / T3.2 / T4.1 etc.)
- load_providers_config(): defaults only, deep merge with user override,
  graceful fallback on missing/corrupt user file, user can add new
  provider that wasn't in bundled defaults.
"""
from __future__ import annotations

import json

import pytest

from bterminal.providers import DEFAULTS_PATH, load_providers_config


# ─── Bundled defaults.json sanity ────────────────────────────────────────────

def test_defaults_file_exists_and_is_valid_json():
    assert DEFAULTS_PATH.exists()
    with open(DEFAULTS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "providers" in data
    assert "default_provider" in data


def test_defaults_has_claude_and_copilot():
    config = load_providers_config()
    assert "claude" in config["providers"]
    assert "copilot" in config["providers"]
    assert config["default_provider"] == "claude"


def test_defaults_claude_capabilities_match_current_behavior():
    """Claude is the established baseline — these flags must stay True
    for T1 to be a true 1:1 abstraction (no behavioral change)."""
    caps = load_providers_config()["providers"]["claude"]["capabilities"]
    assert caps["intro_prompt"] is True
    assert caps["resume_flag"] is True
    assert caps["skip_permissions"] is True
    assert caps["session_log"] is True
    assert caps["usage_api"] is True
    assert caps["rules_inject"] is True
    assert caps["task_auto_trigger"] is True
    assert caps["stats_bar"] is True
    assert caps["context_file"] == "CLAUDE.md"
    assert caps["context_file_cumulative"] is True


def test_defaults_copilot_capabilities_after_t4_2():
    """After T4.2 baseline:
        T2.3: intro_prompt + resume + continue + yolo (build_argv)
        T3.5: session_log + cost_in_log + stats_bar (stats)
        T3.7: rules_inject (PTY feed_child)
        T4.2: task_auto_trigger (events.jsonl idle monitor from T4.1)
    Remaining capabilities flip on in later tasks:
        T4.3: granular_permissions
        T4.4: plan_mode, autopilot
    """
    caps = load_providers_config()["providers"]["copilot"]["capabilities"]
    # Flipped True in T2.3
    assert caps["intro_prompt"] is True
    assert caps["resume_flag"] is True
    assert caps["continue_flag"] is True
    assert caps["skip_permissions"] is True
    # Flipped True in T3.5
    assert caps["session_log"] is True
    assert caps["cost_in_log"] is True
    assert caps["stats_bar"] is True
    # Flipped True in T3.7
    assert caps["rules_inject"] is True
    # Flipped True in T4.2
    assert caps["task_auto_trigger"] is True
    # Static / context
    assert caps["context_file"] == "AGENTS.md"
    assert caps["context_file_cumulative"] is False
    # Path templates set as metadata for future capability flips.
    assert caps["session_log_path"] is not None
    assert caps["session_index_db_path"] is not None


def test_defaults_claude_pricing_includes_known_models():
    pricing = load_providers_config()["providers"]["claude"]["pricing"]
    for model in ("claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"):
        assert model in pricing, f"missing pricing for {model}"
        for k in ("input", "output", "cache_read", "cache_write"):
            assert k in pricing[model], f"{model} missing {k}"
            assert pricing[model][k] >= 0.0


def test_defaults_display_present_for_both():
    providers = load_providers_config()["providers"]
    for name in ("claude", "copilot"):
        d = providers[name]["display"]
        assert d["icon"]
        assert d["short_label"]
        assert d["long_label"]
        assert d["color"].startswith("#")


# ─── load_providers_config() — user override behavior ───────────────────────

def test_load_no_user_path_returns_defaults():
    config = load_providers_config(user_path=None)
    assert "claude" in config["providers"]


def test_load_nonexistent_user_path_falls_back_to_defaults(tmp_path):
    """Missing override file == use defaults (no warning, this is normal)."""
    config = load_providers_config(user_path=tmp_path / "nope.json")
    assert "claude" in config["providers"]
    assert config["default_provider"] == "claude"


def test_user_override_wins_for_overridden_keys(tmp_path):
    """Override flips one Claude flag; everything else stays."""
    user_file = tmp_path / "providers.json"
    user_file.write_text(json.dumps({
        "providers": {
            "claude": {
                "capabilities": {"task_auto_trigger": False}
            }
        }
    }))
    config = load_providers_config(user_path=user_file)
    caps = config["providers"]["claude"]["capabilities"]
    assert caps["task_auto_trigger"] is False           # overridden
    assert caps["intro_prompt"] is True                  # untouched
    assert caps["session_log"] is True                   # untouched
    # Sibling provider untouched — pick a capability that's expected
    # False at current baseline so the assertion proves no leakage
    # rather than coincidentally matching defaults.
    assert config["providers"]["copilot"]["capabilities"]["autopilot"] is False


def test_user_can_add_new_provider(tmp_path):
    """User can append e.g. an aider provider via override."""
    user_file = tmp_path / "providers.json"
    user_file.write_text(json.dumps({
        "providers": {
            "aider": {
                "display": {
                    "icon": "🛠",
                    "short_label": "Aider",
                    "long_label": "Aider",
                    "color": "#f9e2af"
                },
                "capabilities": {"intro_prompt": True}
            }
        }
    }))
    config = load_providers_config(user_path=user_file)
    assert "claude" in config["providers"]
    assert "copilot" in config["providers"]
    assert "aider" in config["providers"]
    assert config["providers"]["aider"]["display"]["icon"] == "🛠"


def test_user_override_replaces_lists_wholesale(tmp_path):
    """List values are NOT merged — they're replaced. User who declares
    a custom binary.search_paths gets exactly what they declared."""
    user_file = tmp_path / "providers.json"
    user_file.write_text(json.dumps({
        "providers": {
            "claude": {
                "binary": {"search_paths": ["/opt/custom/claude"]}
            }
        }
    }))
    config = load_providers_config(user_path=user_file)
    paths = config["providers"]["claude"]["binary"]["search_paths"]
    assert paths == ["/opt/custom/claude"]


def test_corrupt_user_override_falls_back_to_defaults(tmp_path, capsys):
    user_file = tmp_path / "providers.json"
    user_file.write_text("{not valid json at all")
    config = load_providers_config(user_path=user_file)
    # Defaults intact
    assert "claude" in config["providers"]
    assert config["providers"]["claude"]["capabilities"]["intro_prompt"] is True
    # Warning to stderr (don't crash on bad config)
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "providers" in captured.err.lower()


def test_default_provider_can_be_overridden(tmp_path):
    user_file = tmp_path / "providers.json"
    user_file.write_text(json.dumps({"default_provider": "copilot"}))
    config = load_providers_config(user_path=user_file)
    assert config["default_provider"] == "copilot"
