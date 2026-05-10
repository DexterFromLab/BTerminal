"""SessionStatsBar GTK widget (T3.1 — extracted from monolithic stats.py).

Behavior unchanged from pre-T3 code. The reader is now injectable: by
default a ClaudeStatsReader is created (so the widget's public API
stays compatible with `SessionStatsBar(project_dir)`), but T3.5
capability dispatch will pass a Copilot reader for Copilot tabs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from bterminal.ui.stats.base import AbstractStatsReader
from bterminal.ui.stats.claude import ClaudeStatsReader


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_dur(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {sec:02d}s"


def _hidden_label_keys_for_options(hide_plan_usage: bool) -> set[str]:
    """T3.9 pure helper: which `_labels` keys to hide given widget options.

    `hide_plan_usage=True` (Copilot's case — capability
    `stats_bar_no_plan_usage`) drops the 5h/7d gauge labels and the
    two separators that frame them, leaving only the
    `tokens | cost | model` portion of the bar.
    """
    if hide_plan_usage:
        return {"s8", "usage_5h", "s9", "usage_7d"}
    return set()


def _fmt_reset_time(resets_at) -> str:
    """Format a 'plan window resets in X' hint.

    `resets_at` can be a Unix epoch (int/float) or an ISO-8601 string.
    """
    import time as _time_mod
    if isinstance(resets_at, str):
        try:
            dt = datetime.fromisoformat(resets_at)
            epoch = dt.timestamp()
        except (ValueError, TypeError):
            return "?"
    else:
        epoch = float(resets_at)
    diff = epoch - _time_mod.time()
    if diff <= 0:
        return "now"
    if diff < 3600:
        return f"{int(diff / 60)}min"
    hours = diff / 3600
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


class SessionStatsBar(Gtk.Box):
    """Thin status bar showing session metrics for an AI CLI tab.

    The reader is injectable — by default we instantiate a
    ClaudeStatsReader so the legacy `SessionStatsBar(project_dir)`
    constructor still works. T3.5 capability dispatch passes a
    provider-specific reader (CopilotStatsReader from T3.2) for
    non-Claude tabs.
    """

    def __init__(self, project_dir: str,
                 reader: Optional[AbstractStatsReader] = None,
                 hide_plan_usage: bool = False,
                 cost_unavailable: bool = False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._reader = reader or ClaudeStatsReader(project_dir)
        self._prompt_count = 0
        self._timer = 0
        # T3.9: when True, the 5h/7d plan-usage labels are hidden and
        # _update() skips their block entirely. Driven by the
        # provider's `stats_bar_no_plan_usage` capability (Copilot has
        # no public usage API analogous to Claude's).
        self._hide_plan_usage = hide_plan_usage
        self._cost_unavailable = cost_unavailable

        self.set_size_request(-1, 44)

        style = self.get_style_context()
        style.add_class("stats-bar")

        self._labels = {}
        fields = [
            ("dur",      "⏱ --:--",     "Session duration"),
            ("s1",       " │ ",          None),
            ("prompts",  "💬 0",         "Prompts sent"),
            ("s2",       " │ ",          None),
            ("resp",     "🤖 0",         "Responses received"),
            ("s3",       " │ ",          None),
            ("tok_in",   "↑ 0",          "Input tokens (incl. cache writes)"),
            ("s3b",      " ",            None),
            ("tok_out",  "↓ 0",          "Output tokens"),
            ("s4",       " │ ",          None),
            ("cache",    "📦 0%",        "Cache hit rate"),
            ("s5",       " │ ",          None),
            ("cost",     "💰 $0.00",     "Estimated cost"),
            ("s6",       " │ ",          None),
            ("tok_h",    "⚡ 0 tok/h",   "Tokens per hour (throughput)"),
            ("s7",       " │ ",          None),
            ("model",    "",             "Model used"),
            ("s8",       " │ ",          None),
            ("usage_5h", "🔋 5h –",      "Plan usage: current session (5h window)"),
            ("s9",       " ",            None),
            ("usage_7d", "7d –",         "Plan usage: weekly (7d window)"),
        ]
        hidden_keys = _hidden_label_keys_for_options(self._hide_plan_usage)
        for key, text, tooltip in fields:
            lbl = Gtk.Label(label=text)
            lbl.set_margin_start(4)
            lbl.set_margin_end(2)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            if tooltip:
                lbl.set_tooltip_text(tooltip)
                lbl.set_has_tooltip(True)
            self._labels[key] = lbl
            self.pack_start(lbl, False, False, 0)
            if key in hidden_keys:
                # Persist hidden state across show_all() calls.
                lbl.set_no_show_all(True)
                lbl.set_visible(False)

        self.show_all()
        self._timer = GLib.timeout_add(5000, self._update)

    def increment_prompt(self):
        self._prompt_count += 1

    def stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _update(self):
        s = self._reader.read_session_tokens()
        cost = self._reader.read_session_cost(s)

        dur = 0.0
        if s.first_ts:
            end = s.last_ts or datetime.now(timezone.utc)
            dur = (end - s.first_ts).total_seconds()
        total_tok = s.input + s.cache_write + s.cache_read + s.output
        tok_h = total_tok / (dur / 3600) if dur > 1 else 0
        total_in = s.input + s.cache_write
        cache_pct = int(s.cache_read / (total_in + s.cache_read) * 100) \
            if (total_in + s.cache_read) else 0

        self._labels["dur"].set_text(f"⏱ {_fmt_dur(dur)}")
        self._labels["prompts"].set_text(f"💬 {self._prompt_count}")
        self._labels["resp"].set_text(f"🤖 {s.responses}")
        self._labels["tok_in"].set_text(f"↑ {_fmt_tok(total_in)}")
        self._labels["tok_out"].set_text(f"↓ {_fmt_tok(s.output)}")
        self._labels["cache"].set_text(f"📦 {cache_pct}%")
        if self._cost_unavailable:
            # #94: providers with cost_in_log=False (Aider, local LLM)
            # show 'n/a' instead of '$0.0000' so users know the field
            # is intentionally blank rather than a billing error.
            self._labels["cost"].set_text("💰 n/a")
        else:
            self._labels["cost"].set_text(f"💰 ${cost:.4f}")
        self._labels["tok_h"].set_text(f"⚡ {_fmt_tok(int(tok_h))} tok/h")
        if s.model:
            self._labels["model"].set_text(
                s.model.replace("claude-", "").replace("-2024", ""))

        # T3.9: skip plan-usage block entirely when widget is in
        # tokens-only mode (Copilot has no public usage API).
        if self._hide_plan_usage:
            return True

        plan = self._reader.read_plan_usage()
        for key, lbl_key in [("five_hour", "usage_5h"),
                             ("seven_day", "usage_7d")]:
            prefix = "5h" if key == "five_hour" else "7d"
            info = getattr(plan, key, None) if plan else None
            icon = "🔋 " if key == "five_hour" else ""
            if not info:
                self._labels[lbl_key].set_text(f"{icon}{prefix} –")
                self._labels[lbl_key].set_tooltip_text(
                    "Plan usage: current session (5h window)"
                    if key == "five_hour"
                    else "Plan usage: weekly (7d window)"
                )
            else:
                util = info.get("utilization", 0)
                pct = int(util) if util is not None else 0
                resets_at = info.get("resets_at")
                tip = f"{prefix}: {pct}% used"
                if resets_at:
                    tip += f" · resets in {_fmt_reset_time(resets_at)}"
                self._labels[lbl_key].set_text(f"{icon}{prefix} {pct}%")
                self._labels[lbl_key].set_tooltip_text(tip)

        return True


__all__ = ["SessionStatsBar"]
