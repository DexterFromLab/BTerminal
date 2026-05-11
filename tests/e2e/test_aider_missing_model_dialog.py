"""E2E test for BUG#19 — Aider spawn without pre-detection of missing model.

User report (manual QA, 2026-05-11, fresh install on a clean host):
spawning an aider session whose default model `openai/qwen2.5-coder:0.5b`
isn't pulled in local Ollama produces a raw `litellm.NotFoundError:
OpenAIException - model 'qwen2.5-coder:0.5b' not found` inside the VTE
tab. Confusing for any user, fatal for a complete laic.

Direct VM evidence captured 2026-05-11 (smoke-logs/bug19-fix/):
- 03b_aider_litellm_BEFORE_zoom.png: VTE shows litellm.NotFoundError
  after `hello` prompt, exactly matching the host report.
- 04b_dialog_AFTER_zoom.png: same scenario after fix — instead of
  spawning aider, BT shows a 3-button modal warning:
    [Uruchom wizarda] [Pomiń (uruchom mimo to)] [Anuluj]
  ("Wybierz inny model" appears only when `ollama list` is non-empty.)

Fix shape:
- `bterminal/providers/aider.py` gains `is_model_available()` and
  `list_installed_models()` — small helpers parsing `ollama list`.
- `bterminal/ui/terminal_tab.py:spawn_ai_cli` calls
  `_aider_resolve_missing_model(...)` BEFORE _build_spawn_script when
  provider_name == "aider". The helper resolves the would-be model
  via the same chain build_argv uses, checks Ollama, and on miss
  shows a 3-button Gtk.MessageDialog. The caller spawns, picks a
  different model, or aborts based on the dialog response.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Helpers: is_model_available / list_installed_models ──────────────────

def _ollama_list_output(tags: list[str]) -> str:
    """Synthesize the format `ollama list` actually prints (header + rows)."""
    lines = ["NAME                        ID              SIZE      MODIFIED"]
    for tag in tags:
        lines.append(f"{tag:<28}deadbeef        100 MB    1 hour ago")
    return "\n".join(lines) + "\n"


def test_is_model_available_true_when_tag_listed():
    """Pin: `openai/qwen2.5-coder:0.5b` matches the bare tag in `ollama list`
    after stripping the litellm provider prefix."""
    from bterminal.providers.aider import is_model_available
    out = _ollama_list_output(["qwen2.5-coder:0.5b", "llama3:8b"])
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ollama", "list"], returncode=0, stdout=out, stderr="")
        assert is_model_available("openai/qwen2.5-coder:0.5b") is True
        assert is_model_available("ollama/llama3:8b") is True
        assert is_model_available("qwen2.5-coder:0.5b") is True  # no prefix


def test_is_model_available_false_when_tag_missing():
    """Pin: scenario u user-a on 2026-05-11 — `ollama list` is empty or
    doesn't contain the requested tag → must return False so BT can
    short-circuit to the missing-model dialog."""
    from bterminal.providers.aider import is_model_available
    out = _ollama_list_output(["qwen2.5:7b"])  # no qwen2.5-coder
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ollama", "list"], returncode=0, stdout=out, stderr="")
        assert is_model_available("openai/qwen2.5-coder:0.5b") is False


def test_is_model_available_false_when_ollama_binary_missing():
    """Pin: bare-bones machine without Ollama installed at all.
    subprocess raises FileNotFoundError → return False, never crash."""
    from bterminal.providers.aider import is_model_available
    with patch("subprocess.run", side_effect=FileNotFoundError("ollama")):
        assert is_model_available("openai/qwen2.5-coder:0.5b") is False


def test_list_installed_models_parses_header_and_rows():
    """Pin: parser must skip the header line and pick column 0 only —
    real `ollama list` puts ID, SIZE, MODIFIED in subsequent columns."""
    from bterminal.providers.aider import list_installed_models
    out = _ollama_list_output(["qwen2.5-coder:1.5b", "llama3.1:8b", "gpt-oss:20b"])
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ollama", "list"], returncode=0, stdout=out, stderr="")
        got = list_installed_models()
    assert got == ["qwen2.5-coder:1.5b", "llama3.1:8b", "gpt-oss:20b"]


def test_list_installed_models_empty_when_ollama_returns_nonzero():
    """Pin: ollama daemon dead → `ollama list` exits non-zero with an
    error message. Must NOT confuse 'error text' with a model tag."""
    from bterminal.providers.aider import list_installed_models
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ollama", "list"], returncode=1, stdout="",
            stderr="Error: could not connect to ollama server")
        assert list_installed_models() == []


# ── Behavioural: _aider_resolve_missing_model dispatch table ─────────────


class _FakeProvider:
    """Stand-in for AiderProvider — only `.capabilities.default_model` matters
    for the resolve helper; nothing else is touched."""

    class _Caps:
        default_model = "openai/qwen2.5-coder:0.5b"

    capabilities = _Caps()


def _make_tab():
    """Build a TerminalTab stub with just enough attributes for the helper."""
    from bterminal.ui.terminal_tab import TerminalTab
    tab = TerminalTab.__new__(TerminalTab)
    tab.app = None  # MessageDialog is mocked away in these tests
    return tab


def test_resolve_skip_keeps_config_unchanged():
    """Pin: user picks 'Pomiń' — returned config equals input config and
    spawn proceeds (caller will get raw litellm error, but the choice
    was explicit)."""
    tab = _make_tab()
    cfg = {"provider": "aider", "project_dir": "/tmp/x"}
    with patch.object(tab, "_aider_show_missing_model_dialog",
                      return_value=("skip", None)), \
         patch("bterminal.providers.aider.is_model_available",
               return_value=False), \
         patch("bterminal.providers.aider.list_installed_models",
               return_value=[]):
        result = tab._aider_resolve_missing_model(_FakeProvider(), cfg)
    assert result == cfg


def test_resolve_cancel_returns_none():
    """Pin: closing the dialog (X / Anuluj) aborts the spawn entirely —
    helper returns None and the caller short-circuits before
    _build_spawn_script."""
    tab = _make_tab()
    cfg = {"provider": "aider"}
    with patch.object(tab, "_aider_show_missing_model_dialog",
                      return_value=("cancel", None)), \
         patch("bterminal.providers.aider.is_model_available",
               return_value=False), \
         patch("bterminal.providers.aider.list_installed_models",
               return_value=[]):
        assert tab._aider_resolve_missing_model(_FakeProvider(), cfg) is None


def test_resolve_pick_replaces_model_in_opts():
    """Pin: 'Wybierz inny' → picked tag is injected into
    config['provider_options']['model']. Original config dict is NOT
    mutated (defensive copy), so other callers holding the same dict
    don't see surprise changes."""
    tab = _make_tab()
    original = {"provider": "aider",
                "provider_options": {"resume": True}}
    with patch.object(tab, "_aider_show_missing_model_dialog",
                      return_value=("pick", "openai/llama3:8b")), \
         patch("bterminal.providers.aider.is_model_available",
               return_value=False), \
         patch("bterminal.providers.aider.list_installed_models",
               return_value=["llama3:8b"]):
        result = tab._aider_resolve_missing_model(_FakeProvider(), original)
    assert result is not original  # defensive copy
    assert result["provider_options"]["model"] == "openai/llama3:8b"
    assert result["provider_options"]["resume"] is True  # other opts kept


def test_resolve_skips_dialog_when_model_already_installed():
    """Pin: happy path — model IS in `ollama list`, dialog must NOT
    appear (otherwise we'd nag the user on every spawn)."""
    tab = _make_tab()
    cfg = {"provider": "aider"}
    dialog_mock = MagicMock()
    with patch.object(tab, "_aider_show_missing_model_dialog", dialog_mock), \
         patch("bterminal.providers.aider.is_model_available",
               return_value=True):
        result = tab._aider_resolve_missing_model(_FakeProvider(), cfg)
    assert result == cfg
    dialog_mock.assert_not_called()


# ── Model resolution priority chain ──────────────────────────────────────


def test_resolve_model_priority_per_session_opts_first():
    """Pin: opts.model beats every other source. Reproduces the chain
    documented in AiderProvider.build_argv (`# --model resolution
    priority`) — _aider_resolve_model MUST mirror it byte-for-byte
    otherwise the pre-spawn check would test the wrong tag."""
    tab = _make_tab()
    cfg = {"provider_options": {"model": "openai/codellama:7b"}}
    assert tab._aider_resolve_model(_FakeProvider(), cfg) \
        == "openai/codellama:7b"


def test_resolve_model_priority_capabilities_default_when_opts_missing():
    """Pin: no opts.model, no _OPTIONS override → fall back to
    provider.capabilities.default_model (in this test: 'openai/qwen2.5-coder:0.5b')."""
    tab = _make_tab()
    cfg = {"provider_options": {"resume": True}}
    with patch("bterminal.config._OPTIONS", {}):
        got = tab._aider_resolve_model(_FakeProvider(), cfg)
    assert got == "openai/qwen2.5-coder:0.5b"
