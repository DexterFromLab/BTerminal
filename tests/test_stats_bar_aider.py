"""Stats bar parity for Aider — tokens-only mode like Copilot (#22 / #94).

Auto-trigger plan punkty:
  (a) stats_bar_no_plan_usage=True → plan_usage gauge HIDDEN
  (b) AiderStatsReader reads .aider.chat.history.md, extracts tokens
  (c) parse_session_stats roundtrip through stats widget renders
      'in: 1.5k · out: 234' format
  (d) cost_in_log=False → cost field shown as 'n/a'

Headless tests — no GTK widget instantiation. Token extraction is
verified via AiderStatsReader.read_session_tokens() against a real
.aider.chat.history.md file. Widget rendering is verified by calling
the same SessionStatsBar._update logic with a fake-self / fake-reader,
mirroring the pattern used in test_stats_widget_options.py.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bterminal.providers import get_registry
from bterminal.ui.stats import (
    AiderStatsReader,
    SessionStatsBar,
    create_stats_reader_for_ai_config,
    stats_widget_options_for_ai_config,
)
from bterminal.ui.stats.base import TokenStats

REGISTRY = get_registry()


# Representative .aider.chat.history.md content. Aider's actual
# format includes both the heading + Tokens markers + role markers.
SAMPLE_HISTORY = """\
# aider chat
> Tokens: 1.5k sent, 234 received.

#### Reply with PONG.

PONG

#### Add a TODO comment to main.py.

> Tokens: 2,500 sent, 350 received.

I added a TODO comment to main.py:
```diff
+ # TODO: implement
```

aider invocation: --model openai/qwen2.5-coder:0.5b
"""


# ─── (b) AiderStatsReader: reads .aider.chat.history.md, extracts tokens ──


def test_aider_reader_extracts_tokens_from_chat_history(tmp_path):
    """Reader points the provider parser at .aider.chat.history.md and
    converts the resulting SessionStats → TokenStats."""
    (tmp_path / ".aider.chat.history.md").write_text(SAMPLE_HISTORY)
    reader = AiderStatsReader(str(tmp_path))
    stats = reader.read_session_tokens()
    # Two Tokens: lines (1.5k + 2.5k sent → 4k input; 234 + 350 received
    # → 584 output). Aider provider's regex parses 'k' suffix.
    assert stats.input == 1500 + 2500, f"unexpected input: {stats.input}"
    assert stats.output == 234 + 350, f"unexpected output: {stats.output}"
    # Two `#### ` user-turn markers
    assert stats.responses == 2
    # Model attribution from `--model openai/qwen2.5-coder:0.5b` line
    assert stats.model == "openai/qwen2.5-coder:0.5b"
    # Cache tokens stay 0 — Aider's chat log doesn't distinguish them
    assert stats.cache_read == 0
    assert stats.cache_write == 0


def test_aider_reader_empty_when_history_missing(tmp_path):
    """Fresh project, no chat history yet — TokenStats stays at zeros
    (matches the 'pre-first-prompt' visual state)."""
    reader = AiderStatsReader(str(tmp_path))
    stats = reader.read_session_tokens()
    assert stats.input == 0
    assert stats.output == 0
    assert stats.responses == 0
    assert stats.model == ""


def test_aider_reader_handles_empty_project_dir():
    """Empty project_dir (SSH/local tabs) → empty stats, no crash."""
    reader = AiderStatsReader("")
    stats = reader.read_session_tokens()
    assert stats == TokenStats()


def test_aider_reader_resolves_log_path_via_provider_template(tmp_path):
    """Reader uses AiderProvider.session_log_glob — single source of
    truth for the path format. If the provider template changes,
    reader follows automatically."""
    reader = AiderStatsReader(str(tmp_path))
    expected = str(tmp_path) + "/.aider.chat.history.md"
    assert reader._log_path() == expected


def test_aider_reader_no_plan_usage():
    """Plan-usage capability is False — read_plan_usage returns None
    (inherited default). Widget hides 5h/7d block accordingly."""
    reader = AiderStatsReader("/tmp/whatever")
    assert reader.read_plan_usage() is None


def test_aider_reader_zero_cost_regardless_of_tokens():
    """cost_in_log=False — read_session_cost returns 0.0 even when
    tokens are non-zero. Widget renders this as 'n/a' (not '$0.0000')."""
    reader = AiderStatsReader("/tmp/whatever")
    stats = TokenStats(input=10000, output=5000, responses=3)
    assert reader.read_session_cost(stats) == 0.0


# ─── (a) stats_bar_no_plan_usage=True for Aider via factory ──────────────


def test_aider_widget_options_hide_plan_usage_and_mark_cost_unavailable():
    """The factory dispatch for Aider returns BOTH flags:
    hide_plan_usage=True (parity with Copilot) AND
    cost_unavailable=True (Aider's local-LLM nature)."""
    opts = stats_widget_options_for_ai_config(
        {"provider": "aider", "project_dir": "/tmp/p"}, REGISTRY,
    )
    assert opts.get("hide_plan_usage") is True
    assert opts.get("cost_unavailable") is True


def test_factory_returns_aider_reader_instance():
    """create_stats_reader_for_ai_config('aider', ...) returns an
    AiderStatsReader bound to the right project_dir."""
    reader = create_stats_reader_for_ai_config(
        {"provider": "aider", "project_dir": "/tmp/myproj"}, REGISTRY,
    )
    assert isinstance(reader, AiderStatsReader)
    assert reader.project_dir == "/tmp/myproj"


def test_aider_factory_dispatch_parity_with_copilot():
    """Both Aider and Copilot tabs hide plan_usage. Diff vs Claude:
    Claude shows the gauge, Aider/Copilot don't."""
    aider_opts = stats_widget_options_for_ai_config(
        {"provider": "aider"}, REGISTRY)
    copilot_opts = stats_widget_options_for_ai_config(
        {"provider": "copilot"}, REGISTRY)
    claude_opts = stats_widget_options_for_ai_config(
        {"provider": "claude"}, REGISTRY)

    assert aider_opts["hide_plan_usage"] == copilot_opts["hide_plan_usage"]
    assert aider_opts["hide_plan_usage"] != claude_opts["hide_plan_usage"]


# ─── (c) Widget render roundtrip — token format, model render ────────────


def test_widget_update_renders_aider_tokens_in_human_format(tmp_path):
    """Run the SessionStatsBar._update logic against a fake-self with a
    real AiderStatsReader. Verify the human-readable token output
    (`↑ 4k`, `↓ 584`) matches the Aider chat history's data.

    Pattern mirrors test_stats_widget_options.py:test_update_skips_*."""
    (tmp_path / ".aider.chat.history.md").write_text(SAMPLE_HISTORY)
    reader = AiderStatsReader(str(tmp_path))

    # Build a fake `self` with the just-the-attrs SessionStatsBar._update
    # touches. SimpleNamespace + a label dict.
    labels = {}
    for key in ["dur", "prompts", "resp", "tok_in", "tok_out", "cache",
                "cost", "tok_h", "model", "usage_5h", "usage_7d"]:
        labels[key] = SimpleNamespace(
            _text="",
            set_text=lambda txt, k=key: labels[k].__setattr__("_text", txt),
        )
    fake_self = SimpleNamespace(
        _reader=reader,
        _prompt_count=2,
        _hide_plan_usage=True,
        _cost_unavailable=True,
        _labels=labels,
    )
    SessionStatsBar._update(fake_self)

    # tok_in: 1500 + 2500 = 4000 → "4.0K" via _fmt_tok (uppercase
    # K + decimal place is the existing widget format).
    assert "4.0K" in labels["tok_in"]._text or "4000" in labels["tok_in"]._text
    # tok_out: 234 + 350 = 584
    assert "584" in labels["tok_out"]._text
    # responses count
    assert "2" in labels["resp"]._text
    # model rendered (qwen string survives the .replace transformations)
    assert "qwen" in labels["model"]._text


# ─── (d) cost_in_log=False → cost shown as 'n/a' ─────────────────────────


def test_widget_renders_cost_as_na_when_provider_unavailable(tmp_path):
    """When cost_unavailable=True (Aider's case), the widget shows
    `💰 n/a` instead of `💰 $0.0000`. This is the auto-trigger plan's
    explicit 'n/a' contract."""
    (tmp_path / ".aider.chat.history.md").write_text(SAMPLE_HISTORY)
    reader = AiderStatsReader(str(tmp_path))

    labels = {}
    for key in ["dur", "prompts", "resp", "tok_in", "tok_out", "cache",
                "cost", "tok_h", "model", "usage_5h", "usage_7d"]:
        labels[key] = SimpleNamespace(
            _text="",
            set_text=lambda txt, k=key: labels[k].__setattr__("_text", txt),
        )
    fake_self = SimpleNamespace(
        _reader=reader,
        _prompt_count=0,
        _hide_plan_usage=True,
        _cost_unavailable=True,
        _labels=labels,
    )
    SessionStatsBar._update(fake_self)
    assert labels["cost"]._text == "💰 n/a", (
        f"expected 'n/a' rendering, got {labels['cost']._text!r}"
    )


def test_widget_renders_cost_normally_when_provider_has_cost():
    """Negative parity: with cost_unavailable=False (Claude/Copilot's
    case) the widget renders the dollar amount as before. Without
    this test, flipping the default could silently break $-rendering
    for Claude users."""
    # Synthetic reader that returns a real cost
    fake_reader = SimpleNamespace(
        read_session_tokens=lambda: TokenStats(input=100, output=50, responses=1),
        read_session_cost=lambda s: 0.0123,
        read_plan_usage=lambda: None,
    )
    labels = {}
    for key in ["dur", "prompts", "resp", "tok_in", "tok_out", "cache",
                "cost", "tok_h", "model", "usage_5h", "usage_7d"]:
        labels[key] = SimpleNamespace(
            _text="",
            set_text=lambda txt, k=key: labels[k].__setattr__("_text", txt),
            # _hide_plan_usage=False path also calls set_tooltip_text
            # on usage_5h/7d labels — stub it as a no-op.
            set_tooltip_text=lambda txt: None,
        )
    fake_self = SimpleNamespace(
        _reader=fake_reader,
        _prompt_count=1,
        _hide_plan_usage=False,
        _cost_unavailable=False,
        _labels=labels,
    )
    SessionStatsBar._update(fake_self)
    # Format: '💰 $0.0123' (4 decimal places)
    assert "$0.0123" in labels["cost"]._text


# ─── Cross-cutting: capability flag pinning ──────────────────────────────


def test_aider_capability_stats_bar_no_plan_usage_locked_true():
    """Source-of-truth pin: defaults.json has stats_bar_no_plan_usage
    True for Aider (parity with Copilot). #19's parity matrix already
    asserts this — duplicated here so the dedicated #94 test file is
    self-contained against a defaults.json drift."""
    aider = REGISTRY.get("aider")
    assert aider.capabilities.stats_bar_no_plan_usage is True


def test_aider_capability_cost_in_log_locked_false():
    """Source-of-truth pin: cost_in_log=False is what triggers the
    'n/a' rendering path. Locked here to fail loud if anyone toggles
    it without thinking through what it means for the widget."""
    aider = REGISTRY.get("aider")
    assert aider.capabilities.cost_in_log is False
