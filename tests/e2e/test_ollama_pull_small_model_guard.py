"""E2E test for BUG#4 — Pull Ollama dialog accepts <3B models without
warning, despite those models being unusable with aider's edit format.

User report (manual QA, 2026-05-10): with `qwen2.5-coder:0.5b` selected,
aider repeatedly emits "The LLM did not conform to the edit format" and
returns placeholder responses. 0.5B-parameter models physically cannot
follow aider's structured-edit conventions; users should be warned at
PULL time, not after wasting 5+ minutes downloading the model.

Behavioural manifestation:
  Pull Ollama model dialog → user types `qwen2.5-coder:0.5b` → hits Pull →
  ollama-pull starts immediately with no size check.

Two reasonable fixes (both shipped together is best):
  (a) Hint at the dialog level: "Models below 3B param often fail
      with aider's edit format. Continue anyway?" — non-blocking
      confirmation prompt.
  (b) Block at pull start with the same message + Yes/No before the
      blocking modal spinner appears.

This test pins the contract: a helper `_model_param_count_b(tag)`
parses common ollama tag suffixes; `_on_pull_model` consults it and
either warns or blocks before triggering the actual pull.

Run on VM for behavioural evidence: launch BT, open Pull dialog,
type the small-model tag, screenshot the result. Visual review
through Read tool confirms no warning is shown today.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPTIONS_PY = REPO_ROOT / "bterminal" / "ui" / "dialogs" / "options.py"


# ── Layer 1 — Helper contract: parse param count from tag ────────────────


def test_helper_function_parses_common_ollama_tag_sizes():
    """Pin: a helper that maps ollama tags like 'qwen2.5-coder:0.5b'
    or 'llama3.1:8b' to a numeric parameter count must exist. Without
    it the dialog has no way to gate by size."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    assert "_model_param_count_b" in src or \
           "_param_count_billions" in src or \
           "_model_size_b" in src, (
        "Expected a helper like `_model_param_count_b(tag) -> float|None` "
        "in bterminal/ui/dialogs/options.py. Today the Pull Ollama "
        "dialog has no model-size logic — adding a parser is the first "
        "step toward the warning/block flow."
    )


def test_helper_handles_known_tags_correctly():
    """Pin: import the helper and run it on a basket of real tags
    sourced from the ollama library. If the function name differs,
    update the import path here (one of the test_helper_function_…
    candidates is the source of truth)."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    if "_model_param_count_b" not in src \
       and "_param_count_billions" not in src \
       and "_model_size_b" not in src:
        pytest.skip("helper not implemented yet — this test waits "
                    "for the fix to land")

    # Try common names in priority order
    helper = None
    for name in ("_model_param_count_b", "_param_count_billions",
                 "_model_size_b"):
        try:
            from bterminal.ui.dialogs import options as opts_mod
            helper = getattr(opts_mod, name, None)
            if helper:
                break
        except Exception:
            pass
    assert helper, "helper not importable"

    cases = [
        ("qwen2.5-coder:0.5b", 0.5),
        ("qwen2.5-coder:3b", 3.0),
        ("qwen2.5-coder:7b", 7.0),
        ("llama3.1:8b", 8.0),
        ("deepseek-coder-v2:16b", 16.0),
        ("llama3.1:70b", 70.0),
    ]
    for tag, expected in cases:
        got = helper(tag)
        assert got == expected, (
            f"helper({tag!r}) returned {got!r}, expected {expected}"
        )

    # Tags without a recognisable size → None (caller treats as
    # "unknown, don't block").
    assert helper("custom:latest") is None
    assert helper("model_no_size") is None


# ── Layer 2 — Dialog flow guard ──────────────────────────────────────────


def test_on_pull_model_consults_size_helper_before_pulling():
    """Pin: `_on_pull_model` must reference the size helper somewhere
    between reading the entry text and calling _pull_model_blocking.
    Sourcecode-level grep is enough — once the helper is wired, the
    behavioural test below picks up the actual UI surfacing."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    # Slice _on_pull_model body
    start = src.find("def _on_pull_model")
    assert start > 0
    end = src.find("\n    def ", start + 1)
    body = src[start:end]

    # The body must reference the size helper OR include a literal
    # threshold check (3 / 3.0 / "0.5b" pattern)
    has_helper_ref = bool(
        re.search(r"_model_param_count_b|_param_count_billions|_model_size_b",
                  body))
    has_threshold_literal = bool(
        re.search(r"\b3(\.0)?\b.*(small|param|size|tiny)|"
                  r"(small|param|size|tiny).*\b3(\.0)?\b",
                  body, re.IGNORECASE))
    assert has_helper_ref or has_threshold_literal, (
        f"`_on_pull_model` neither calls a size helper nor checks a "
        f"3B threshold literal. Today the dialog accepts any tag "
        f"and immediately fires `ollama pull`. Body:\n{body[:600]}"
    )


def test_on_pull_model_uses_warning_dialog_for_small_models():
    """Pin: when a size guard is wired, the small-model branch must
    surface a confirm/warn dialog. Skipped while the guard helper
    isn't even referenced — the previous test already covers that
    gate. Once a helper ref appears, this test starts enforcing the
    dialog wiring."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    start = src.find("def _on_pull_model")
    end = src.find("\n    def ", start + 1)
    body = src[start:end]

    has_helper_ref = bool(re.search(
        r"_model_param_count_b|_param_count_billions|_model_size_b",
        body))
    if not has_helper_ref:
        pytest.skip("size helper not referenced yet — see prior "
                    "test; dialog wiring is the next layer")

    # A confirm/warn flow needs either a MessageDialog with WARNING/
    # QUESTION type or a second dialog whose response is checked.
    has_warn_construct = bool(re.search(
        r"MessageDialog\([^)]*(WARNING|QUESTION)|"
        r"YES_NO|"
        r"_warn_small_model|"
        r"confirm_small_model",
        body))
    assert has_warn_construct, (
        f"size guard exists (helper referenced) but no MessageDialog "
        f"WARNING/QUESTION nor explicit confirm helper detected in "
        f"_on_pull_model. Users need a clear Yes/No before pull. "
        f"Body:\n{body[:600]}"
    )


# ── Documentation guard — the dialog label/placeholder must hint ────────


def test_pull_dialog_label_warns_about_small_models():
    """Pin: the dialog's label or placeholder text should hint that
    sub-3B models won't work with aider. This is the cheapest fix:
    even if the runtime guard isn't there, at least informed users
    avoid the trap."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    start = src.find("def _on_pull_model")
    end = src.find("\n    def ", start + 1)
    body = src[start:end]

    hints = ("3b", "small model", "edit format", "below 3", "<3b",
             "≥ 3", "at least 3")
    found = any(h in body.lower() for h in hints)
    assert found, (
        "dialog text doesn't warn about small models. Even a single "
        "line in the label like 'Models below 3B param may fail with "
        "aider' would meaningfully reduce user pain. Current label "
        "only mentions example tags as hints."
    )
