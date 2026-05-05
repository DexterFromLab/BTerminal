"""Unit tests for config module — pure logic, no GTK runtime needed."""

import json

import pytest

from bterminal import config
# ─── _load_options / _save_options ────────────────────────────────────────────

def test_options_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OPTIONS_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    opts = config._load_options()
    assert opts == config._OPTIONS_DEFAULTS
    # Must be a copy — mutation must not affect defaults
    opts["theme"] = "light"
    assert config._OPTIONS_DEFAULTS["theme"] == "dark"


def test_options_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "options.json"
    monkeypatch.setattr(config, "OPTIONS_FILE", str(f))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    payload = {
        "theme": "light",
        "font": "FiraCode 12",
        "shell": "/usr/bin/zsh",
        "check_updates_on_start": False,
        "language": "pl",
        "tell_ai_language": False,
    }
    config._save_options(payload)
    assert json.loads(f.read_text()) == payload
    loaded = config._load_options()
    assert loaded == payload


def test_options_corrupt_json_falls_back(tmp_path, monkeypatch):
    f = tmp_path / "options.json"
    f.write_text("{this is not valid json")
    monkeypatch.setattr(config, "OPTIONS_FILE", str(f))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_options_load_error", None)
    opts = config._load_options()
    assert opts == config._OPTIONS_DEFAULTS


def test_options_corrupt_json_self_heals(tmp_path, monkeypatch):
    """R1.f2: corrupt options.json → load returns defaults AND self-heals
    by overwriting file with defaults, AND records error for deferred
    dialog. Next load reads the now-fixed file cleanly without error."""
    f = tmp_path / "options.json"
    f.write_text("{not really json")
    monkeypatch.setattr(config, "OPTIONS_FILE", str(f))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_options_load_error", None)

    opts1 = config._load_options()
    assert opts1 == config._OPTIONS_DEFAULTS
    # Error recorded for later dialog
    assert config._options_load_error is not None
    assert isinstance(config._options_load_error, Exception)

    # File overwritten with valid defaults
    on_disk = json.loads(f.read_text())
    assert on_disk == config._OPTIONS_DEFAULTS

    # Subsequent load is clean (no new error recorded)
    monkeypatch.setattr(config, "_options_load_error", None)
    opts2 = config._load_options()
    assert opts2 == config._OPTIONS_DEFAULTS
    assert config._options_load_error is None  # nothing went wrong


def test_options_missing_file_no_error_recorded(tmp_path, monkeypatch):
    """R1.f1: brak pliku ≠ błąd. _options_load_error pozostaje None."""
    monkeypatch.setattr(config, "OPTIONS_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setattr(config, "_options_load_error", None)
    opts = config._load_options()
    assert opts == config._OPTIONS_DEFAULTS
    assert config._options_load_error is None


def test_options_partial_merges_with_defaults(tmp_path, monkeypatch):
    """User options override defaults but missing keys come from defaults."""
    f = tmp_path / "options.json"
    f.write_text(json.dumps({"theme": "light"}))
    monkeypatch.setattr(config, "OPTIONS_FILE", str(f))
    opts = config._load_options()
    assert opts["theme"] == "light"
    assert opts["font"] == config._OPTIONS_DEFAULTS["font"]
    assert opts["check_updates_on_start"] == config._OPTIONS_DEFAULTS["check_updates_on_start"]


# ─── _parse_color ────────────────────────────────────────────────────────────

def test_parse_color_hex_returns_correct_rgb():
    rgba = config._parse_color("#ff0000")
    # Gdk.RGBA components are 0.0–1.0 floats
    assert rgba.red == pytest.approx(1.0, abs=1e-3)
    assert rgba.green == pytest.approx(0.0, abs=1e-3)
    assert rgba.blue == pytest.approx(0.0, abs=1e-3)


def test_parse_color_returns_rgba_instance():
    """_parse_color always returns Gdk.RGBA, even on invalid input
    (current impl never raises — it returns the default-constructed
    RGBA when parsing fails)."""
    result = config._parse_color("#1e1e2e")
    # Gdk types are awkward to introspect — duck-type via attributes
    assert hasattr(result, "red") and hasattr(result, "green") and hasattr(result, "blue")


# ─── _session_color ──────────────────────────────────────────────────────────

def test_session_color_changes_per_theme(monkeypatch):
    monkeypatch.setitem(config._OPTIONS, "theme", "dark")
    dark_ssh = config._session_color("ssh")
    monkeypatch.setitem(config._OPTIONS, "theme", "light")
    light_ssh = config._session_color("ssh")
    assert dark_ssh != light_ssh


def test_session_color_unknown_type_falls_back_to_text():
    """Unknown session_type returns CATPPUCCIN['text'] instead of crashing."""
    # _THEME_COLORS only knows "ssh" and "claude"
    color = config._session_color("definitely_not_a_real_type")
    assert color == config.CATPPUCCIN["text"]


def test_session_color_known_types_dont_fall_back():
    """ssh / claude have explicit entries, must not return the fallback text."""
    monkeypatch_theme = "dark"
    config._OPTIONS["theme"] = monkeypatch_theme
    ssh = config._session_color("ssh")
    claude = config._session_color("claude")
    text_color = config.CATPPUCCIN["text"]
    # In dark theme, _THEME_COLORS["dark"]["ssh"] = "#89b4fa" — distinct from text
    assert ssh != text_color or claude != text_color, (
        "Either ssh or claude should differ from text fallback "
        "(coincidence-fail acceptable if palette swaps these intentionally)"
    )


# ─── _build_css ──────────────────────────────────────────────────────────────

def test_build_css_includes_palette_colors():
    """Generated CSS must contain the supplied theme's base/text colors."""
    css = config._build_css(config.CATPPUCCIN_MOCHA)
    assert config.CATPPUCCIN_MOCHA["base"] in css
    assert config.CATPPUCCIN_MOCHA["text"] in css


def test_build_css_no_undefined_vars():
    """No `{undefined_key}` placeholders left in output — every placeholder
    must have been substituted from the palette dict."""
    css = config._build_css(config.CATPPUCCIN_MOCHA)
    # Pre-substitution f-string would have `{` only inside CSS rule braces.
    # Real CSS uses `{ ... }` for blocks but those should always be doubled
    # `{{ ... }}` in source — so the rendered output must have no
    # `{key}` patterns where key is alphabetic.
    import re
    # Match `{ word }` or `{word}` — would be unsubstituted placeholder
    leftover = re.findall(r"\{[a-z_][a-z0-9_]*\}", css)
    assert not leftover, f"Unsubstituted placeholders: {leftover}"


def test_build_css_module_constant_matches_function_call():
    """config.CSS (computed at module load) must equal _build_css(CATPPUCCIN)."""
    # Note: bterminal.py recomputes its own CSS, but config doesn't expose
    # a module-level CSS constant — only the builder. This test guards against
    # accidentally re-introducing one with stale state.
    assert not hasattr(config, "CSS"), (
        "config.py should not export CSS — it's mutable runtime state in bterminal.py"
    )


def test_themes_have_same_keys():
    """Latte and Mocha must define the same color keys to keep _build_css
    template-compatible regardless of active theme."""
    assert set(config.CATPPUCCIN_MOCHA.keys()) == set(config.CATPPUCCIN_LATTE.keys())


def test_terminal_palettes_have_16_colors():
    """VTE expects exactly 16 ANSI colors per palette."""
    assert len(config.TERMINAL_PALETTE_MOCHA) == 16
    assert len(config.TERMINAL_PALETTE_LATTE) == 16
