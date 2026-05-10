"""i18n: tell_ai_language footer for Aider sessions
(#47 / #119, audit § 6.4 #20).

When BT runs in a non-English locale AND the user has
`tell_ai_language=True` (default in config.py), the intro_prompt
that BT computes for each AI tab gets a footer appended:

    --- User language ---
    The user prefers to communicate in <Language>. Respond in
    that language unless the user switches.

This text is hardcoded English in the prompt (the AI receives
English instructions; the user's language is named for the model
to know what to switch INTO). The hint is provider-agnostic —
applied uniformly through `_build_intro_prompt`, which is called
by `_compute_intro_prompt_for_tab` for every provider.

For Aider specifically, the intro_prompt reaches the model via
`stdin_feed` mode (#3 / audit § 1) — BT injects the prompt via
PTY after spawn, NOT via argv. The footer rides along.

Three decision branches:
  (a) lang=pl + tell_ai_language=True → footer present, names
      "Polish".
  (b) lang=en → NO footer (English IS the AI's working language;
      no switch needed).
  (c) lang=de → footer present, names "German".

Plus disabled-toggle pin: tell_ai_language=False → NO footer
regardless of locale.

Pinned defenses:
  - Footer is identical for Claude/Copilot/Aider — comes from
    `_build_intro_prompt` which has no provider-specific branches
    on the language-hint code path.
  - `language_english_name` returns the canonical English name
    (e.g. "Polish" for "pl") that the model recognizes.
  - The footer joins the existing intro WITHOUT duplicating
    headers (single `--- User language ---` section).

Manual VM smoke (`BTERMINAL_LANG=pl xvfb-run python3 -m bterminal`,
spawn aider, observe intro_prompt) is documented in tests/manual/
README.md.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
CC = "bterminal.ui.dialogs.claude_code"


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


def _seed_ctx_db(tmp_path, project_name="myproj"):
    """Minimal CTX DB so _resolve_ctx_project_name works."""
    ctx_db = tmp_path / "context.db"
    conn = sqlite3.connect(str(ctx_db))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                name TEXT PRIMARY KEY, description TEXT,
                work_dir TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, key)
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(name, description, work_dir) VALUES (?, ?, ?)",
            (project_name, "test", str(tmp_path / project_name)),
        )
        conn.commit()
    finally:
        conn.close()
    return ctx_db


def _stub_app():
    """Minimal `app` for _compute_intro_prompt_for_tab."""
    return SimpleNamespace(_plugins={}, sidecar_manifests={})


def _build_aider_tab(project_dir):
    """Stubbed tab object with aider provider config."""
    return SimpleNamespace(
        ai_config={
            "provider": "aider",
            "name": "MyAiderSession",
            "project_dir": str(project_dir),
        },
        enabled_plugins=None,
    )


def _stub_intro_helpers(monkeypatch):
    """Stub the deep deps of _build_intro_prompt — fetch_ctx_output
    + rules + tools — they hit subprocess. Returns empty for all,
    so we test ONLY the language-footer branch."""
    monkeypatch.setattr(f"{CC}._fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(f"{CC}._fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(f"{CC}._read_global_rules", lambda: [])
    monkeypatch.setattr(f"{CC}._tools_help", lambda _p: "(tools)")


# ─── Branch (a): lang=pl + tell_ai_language=True → footer ───────────────


def test_polish_locale_appends_footer_to_aider_intro_prompt(
        tmp_path, monkeypatch):
    """`current_language()` returns 'pl' + option enabled →
    intro_prompt for an aider tab includes the language footer
    naming "Polish"."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)

    # Force language=pl and tell_ai_language=True
    monkeypatch.setattr(f"{CC}.current_language", lambda: "pl")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    # Footer header marker present
    assert "--- User language ---" in out
    # Polish named
    assert "Polish" in out, (
        f"footer doesn't mention Polish. Output ends with:\n"
        f"{out[-500:]!r}"
    )
    # Switch hint present
    assert "Respond in that language" in out


# ─── Branch (b): lang=en → NO footer ────────────────────────────────────


def test_english_locale_does_not_append_footer(tmp_path, monkeypatch):
    """`current_language()` returns 'en' (BT's default) → NO
    language footer (the AI's working language IS English; no
    switch needed)."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)

    monkeypatch.setattr(f"{CC}.current_language", lambda: "en")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    # No footer
    assert "--- User language ---" not in out
    assert "Respond in that language" not in out


# ─── Branch (c): lang=de → footer with German ───────────────────────────


def test_german_locale_appends_footer_with_german_name(
        tmp_path, monkeypatch):
    """Same as (a) for de → "German"."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)

    monkeypatch.setattr(f"{CC}.current_language", lambda: "de")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    assert "--- User language ---" in out
    assert "German" in out


@pytest.mark.parametrize("lang_code, lang_name", [
    ("pl", "Polish"),
    ("de", "German"),
    ("fr", "French"),
    ("es", "Spanish"),
    ("it", "Italian"),
])
def test_other_locales_use_canonical_english_name(
        lang_code, lang_name, tmp_path, monkeypatch):
    """Cross-language matrix: each supported locale produces the
    matching English name in the footer. Pin so a refactor that
    e.g. switches to native names ("Polski") forces explicit
    audit."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)

    monkeypatch.setattr(f"{CC}.current_language", lambda: lang_code)
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    # Skip if language_english_name doesn't know this code (it
    # falls back to the code itself — different scenario)
    from bterminal.i18n import language_english_name
    if language_english_name(lang_code) == lang_code:
        pytest.skip(f"{lang_code} not in SUPPORTED_LANGUAGES")
    assert lang_name in out


# ─── Disabled toggle: tell_ai_language=False → NO footer ────────────────


def test_disabled_toggle_suppresses_footer_under_polish_locale(
        tmp_path, monkeypatch):
    """User opted out via Options → no footer regardless of
    locale. Pin so the toggle keeps its kill-switch semantics."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)

    monkeypatch.setattr(f"{CC}.current_language", lambda: "pl")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", False)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    assert "--- User language ---" not in out
    assert "Polish" not in out


# ─── Provider parity: same footer for Claude / Copilot / Aider ─────────


@pytest.mark.parametrize("provider", ["claude", "copilot", "aider"])
def test_language_footer_appears_for_all_three_providers(
        provider, tmp_path, monkeypatch):
    """The footer is emitted by `_build_intro_prompt` — identical
    for all providers. Pin the provider-agnostic property."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)

    monkeypatch.setattr(f"{CC}.current_language", lambda: "pl")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / f"proj-{provider}"
    project_dir.mkdir()
    tab = SimpleNamespace(
        ai_config={
            "provider": provider,
            "name": f"{provider}Session",
            "project_dir": str(project_dir),
        },
        enabled_plugins=None,
    )
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)
    assert "--- User language ---" in out
    assert "Polish" in out


def test_footer_is_byte_identical_across_providers(
        tmp_path, monkeypatch):
    """Cross-cutting: extract the footer from each provider's
    intro_prompt → byte-identical strings. The footer doesn't
    interpolate the provider name."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)

    monkeypatch.setattr(f"{CC}.current_language", lambda: "de")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab

    footers = {}
    for provider in ("claude", "copilot", "aider"):
        project_dir = tmp_path / f"proj-{provider}"
        project_dir.mkdir(exist_ok=True)
        tab = SimpleNamespace(
            ai_config={
                "provider": provider,
                "name": f"{provider}S",
                "project_dir": str(project_dir),
            },
            enabled_plugins=None,
        )
        out = _compute_intro_prompt_for_tab(_stub_app(), tab)
        footer_idx = out.find("--- User language ---")
        assert footer_idx > 0
        footers[provider] = out[footer_idx:]

    # All footers identical
    first = footers["claude"]
    for name, footer in footers.items():
        assert footer == first, (
            f"{name} footer diverged from claude:\n"
            f"  claude: {first!r}\n"
            f"  {name}: {footer!r}"
        )


# ─── Aider-specific: footer rides via stdin_feed mode ───────────────────


def test_aider_intro_prompt_mode_is_stdin_feed():
    """Pin: aider's `intro_prompt_mode` is `stdin_feed` —
    intro_prompt (including the language footer) is injected
    via PTY after spawn, NOT through argv. Cross-ref #24/#96."""
    reg = ProviderRegistry(config=load_providers_config())
    spec = reg.get("aider")._argv_spec
    assert spec.get("intro_prompt_mode") == "stdin_feed"


def test_aider_build_argv_does_not_carry_intro_prompt_with_footer(
        tmp_path):
    """Pin: even with the language footer in the intro_prompt,
    AiderProvider.build_argv DOES NOT include any of the footer
    text in argv. The footer reaches aider only via stdin_feed."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    aider._binary_spec["binary"] = "/tmp/aider"

    intro_with_footer = (
        "Project name in ctx/tasks: myproj\n\n"
        "--- User language ---\n"
        "The user prefers to communicate in Polish. "
        "Respond in that language unless the user switches."
    )
    argv = aider.build_argv(
        {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt=intro_with_footer,
    )
    # Footer text NOT in argv
    for needle in ("User language", "Polish", "Respond in that"):
        for arg in argv:
            assert needle not in arg, (
                f"{needle!r} leaked into Aider argv element {arg!r}"
            )


# ─── Source-grep: footer logic exists, hardcoded EN by design ───────────


def test_build_intro_prompt_appends_language_footer_under_options():
    """Source-grep: `_build_intro_prompt` has the footer branch
    gated by both `_OPTIONS.get("tell_ai_language", True)` AND
    `ui_lang != "en"`. Pin both gates."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "claude_code.py").read_text()
    fn_start = src.find("def _build_intro_prompt")
    fn_end = src.find("\nclass ", fn_start)
    body = src[fn_start:fn_end]
    assert '_OPTIONS.get("tell_ai_language"' in body
    assert "ui_lang != \"en\"" in body or 'ui_lang != "en"' in body
    assert "--- User language ---" in body
    assert "Respond in that language" in body


def test_footer_text_is_hardcoded_english_for_model_consumption():
    """Pin: the footer instruction is in English (the AI's
    working language). The user's preferred language is
    INTERPOLATED as an English name (Polish, German, ...)
    — not native (Polski, Deutsch).

    Reason: the model receives instructions in English; only
    the LANGUAGE NAME tells it what to switch into."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "claude_code.py").read_text()
    fn_start = src.find("def _build_intro_prompt")
    fn_end = src.find("\nclass ", fn_start)
    body = src[fn_start:fn_end]
    # Hardcoded English instruction
    assert "The user prefers to communicate in" in body
    # And language_english_name is what fills the placeholder
    assert "language_english_name" in body


def test_default_tell_ai_language_is_true_in_options():
    """Pin: `tell_ai_language` default is True in config.py
    _OPTIONS_DEFAULTS. New users with non-English locale get
    the footer automatically."""
    src = (REPO_ROOT / "bterminal" / "config.py").read_text()
    # Default value True somewhere near the option key
    import re
    m = re.search(r'"tell_ai_language"\s*:\s*(True|False)', src)
    assert m, "tell_ai_language default not in config.py _OPTIONS"
    assert m.group(1) == "True", (
        "tell_ai_language default flipped to False — non-English "
        "users no longer get the language hint by default"
    )


# ─── Aider footer reaches model via PTY (cross-ref #24, #117) ───────────


def test_compute_intro_prompt_for_tab_returns_string_for_pty_feed(
        tmp_path, monkeypatch):
    """Pin: `_compute_intro_prompt_for_tab` returns str (not
    bytes). The Aider spawn flow encodes the str → bytes via
    PTY's stdin_feed write. Pin so a refactor that pre-encodes
    can't double-encode the Polish language name."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)
    monkeypatch.setattr(f"{CC}.current_language", lambda: "pl")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    assert isinstance(out, str)
    # UTF-8 round-trip safe (Polish in language name + content)
    encoded = out.encode("utf-8")
    decoded = encoded.decode("utf-8")
    assert decoded == out


def test_aider_intro_with_polish_content_and_footer_round_trips(
        tmp_path, monkeypatch):
    """End-to-end: project with Polish CLAUDE.md content +
    `current_language()=pl` → intro_prompt has BOTH the Polish
    project content AND the (English) language footer."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))

    # ctx_output is Polish; rules also Polish
    monkeypatch.setattr(
        f"{CC}._fetch_ctx_output",
        lambda _p: "shared.cel: Zaawansowany analizator słów ąęć",
    )
    monkeypatch.setattr(
        f"{CC}._fetch_rules_block",
        lambda _p: "## Reguły\n\n- Bądź zwięzły — używaj polskiego.",
    )
    monkeypatch.setattr(f"{CC}._read_global_rules", lambda: [])
    monkeypatch.setattr(f"{CC}._tools_help", lambda _p: "(tools)")

    monkeypatch.setattr(f"{CC}.current_language", lambda: "pl")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    # Polish content embedded
    assert "ąęć" in out
    assert "Bądź zwięzły" in out
    # English footer (hardcoded EN) referencing Polish
    assert "--- User language ---" in out
    assert "Polish" in out
    assert "Respond in that language" in out
    # Aider header label (cross-ref #24)
    assert "Aider" in out


# ─── Edge case: unknown locale code falls through to code itself ────────


def test_unknown_locale_code_uses_code_as_name(tmp_path, monkeypatch):
    """`language_english_name` returns the code itself when not
    in SUPPORTED_LANGUAGES. Pin: footer renders with the raw
    code rather than crashing or omitting."""
    ctx_db = _seed_ctx_db(tmp_path)
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    _stub_intro_helpers(monkeypatch)
    monkeypatch.setattr(f"{CC}.current_language", lambda: "xx")  # not real
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    tab = _build_aider_tab(project_dir)
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)

    # Footer present (since lang != en), code embedded as fallback
    assert "--- User language ---" in out
    assert "communicate in xx" in out


# ─── No footer when no project_dir (SSH/local tabs) ────────────────────


def test_no_footer_when_ai_config_lacks_project_dir(monkeypatch):
    """Without a project_dir, _compute_intro_prompt_for_tab
    returns custom_prompt only (or empty). Footer logic lives
    inside _build_intro_prompt which only runs when project_dir
    exists. Pin so SSH tabs don't accidentally pick up the AI
    language hint."""
    monkeypatch.setattr(f"{CC}.current_language", lambda: "pl")
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "tell_ai_language", True)

    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = SimpleNamespace(
        ai_config={
            "provider": "aider",
            "name": "X",
            "prompt": "custom user prompt",
            # NO project_dir
        },
        enabled_plugins=None,
    )
    out = _compute_intro_prompt_for_tab(_stub_app(), tab)
    # Just custom_prompt, no footer
    assert out == "custom user prompt"
    assert "User language" not in out
