"""Tests for bterminal.diagnostics — installer + runtime dep audit (#62).

Pure helpers only — GTK dialog rendering tested via manual smoke
(opening Tools → Diagnostics in the live app on VM).

Coverage:
  - DEPENDENCIES registry contains every system tool install.sh
    references (parity check)
  - detect_tool present/missing branches
  - audit() returns one DepStatus per dep, in registry order
  - format_summary_text() produces the expected layout (tier headers
    + check / cross marks + feature-disabled line for missing)
  - missing_features() filters to feature-blocker (auto tier) only
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal.diagnostics import (
    DEPENDENCIES,
    DepSpec,
    DepStatus,
    audit,
    detect_tool,
    format_summary_text,
    missing_features,
)


# ─── Registry shape ─────────────────────────────────────────────────────────


def test_dependencies_registry_is_non_empty_and_immutable():
    assert isinstance(DEPENDENCIES, tuple)
    assert len(DEPENDENCIES) >= 9, (
        f"task #62 expects at least 9 system deps; got {len(DEPENDENCIES)}"
    )


def test_every_dep_has_required_fields():
    for d in DEPENDENCIES:
        assert isinstance(d, DepSpec)
        assert d.cmd, "every dep needs a cmd"
        assert d.label, "every dep needs a human label"
        assert d.tier in ("required", "auto", "optional"), (
            f"unknown tier for {d.cmd}: {d.tier}"
        )
        # auto tier deps MUST describe what breaks when missing —
        # otherwise the [SUMMARY] block can't tell the user
        if d.tier == "auto":
            assert d.feature, f"auto-tier dep {d.cmd} must declare a feature"


def test_dependencies_match_install_sh_check_tool_calls():
    """Parity check: every check_tool line in install.sh names a cmd
    that the Python registry knows about. Catches drift when one side
    is updated without the other."""
    install_sh = (REPO_ROOT / "install.sh").read_text()
    sh_cmds = set()
    for line in install_sh.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("check_tool"):
            continue
        # Skip function definition / declaration: `check_tool() { … }`
        # — only count actual invocation rows like `check_tool meld …`
        if "(" in stripped.split(None, 1)[0]:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            sh_cmds.add(parts[1])

    py_cmds = {d.cmd for d in DEPENDENCIES}

    missing_in_py = sh_cmds - py_cmds
    missing_in_sh = py_cmds - sh_cmds
    assert not missing_in_py, (
        f"install.sh check_tool calls {missing_in_py} have no DepSpec entry"
    )
    assert not missing_in_sh, (
        f"DepSpec entries {missing_in_sh} not in install.sh — "
        f"either remove or add corresponding check_tool"
    )


def test_required_tier_includes_git_and_ssh():
    """Sanity: git and ssh must be tier='required' — install.sh aborts
    on their absence; downgrading them to 'auto' would silently degrade
    BT instead of failing fast."""
    by_cmd = {d.cmd: d for d in DEPENDENCIES}
    assert by_cmd["git"].tier == "required"
    assert by_cmd["ssh"].tier == "required"


def test_meld_is_auto_tier_with_feature_description():
    """Specific to the user's bug report (2026-05-07): meld must be
    in the registry with a clear feature description so the SUMMARY
    block tells the user what they lose."""
    meld = next((d for d in DEPENDENCIES if d.cmd == "meld"), None)
    assert meld is not None, "meld missing from DEPENDENCIES"
    assert meld.tier == "auto"
    assert "diff" in meld.feature.lower() or "merge" in meld.feature.lower()


# ─── detect_tool branches ───────────────────────────────────────────────────


def test_detect_tool_returns_present_false_for_missing_command():
    spec = DepSpec("definitely-not-a-real-binary-xyz", "n/a", "X", "auto", "broken")
    st = detect_tool(spec)
    assert st.present is False
    assert st.path is None
    assert st.version is None


def test_detect_tool_returns_present_true_for_known_command():
    """sh is on every POSIX system."""
    spec = DepSpec("sh", "dash", "POSIX shell", "required", "")
    st = detect_tool(spec)
    assert st.present is True
    assert st.path is not None
    # version is best-effort; sh may or may not respond to --version


def test_detect_tool_swallows_subprocess_failures(monkeypatch):
    """A binary that hangs or crashes on --version must still report
    as present (path resolved) without raising."""
    spec = DepSpec("ls", "coreutils", "ls", "required", "")
    # Monkey-patch run to simulate a crash
    import subprocess as sp
    def raise_oserror(*a, **k):
        raise OSError("simulated")
    monkeypatch.setattr(sp, "run", raise_oserror)
    # Re-import fresh to use patched run
    from bterminal import diagnostics
    monkeypatch.setattr(diagnostics.subprocess, "run", raise_oserror)
    st = diagnostics.detect_tool(spec)
    assert st.present is True
    assert st.version is None


# ─── audit() ────────────────────────────────────────────────────────────────


def test_audit_returns_status_per_dependency_in_order():
    out = audit()
    assert len(out) == len(DEPENDENCIES)
    for s, d in zip(out, DEPENDENCIES):
        assert s.spec is d
        assert isinstance(s, DepStatus)


def test_audit_accepts_custom_dep_list():
    deps = (
        DepSpec("sh", "dash", "sh", "required", ""),
        DepSpec("not-a-real-binary-zzz", "x", "Fake", "auto", "Fake feature"),
    )
    out = audit(deps)
    assert len(out) == 2
    assert out[0].present is True
    assert out[1].present is False


# ─── format_summary_text ────────────────────────────────────────────────────


def test_format_summary_starts_with_summary_marker():
    out = format_summary_text(audit())
    assert out.startswith("[SUMMARY]"), out[:200]


def test_format_summary_groups_by_tier():
    """Tier headers appear in canonical order: Required → Auto → Optional."""
    out = format_summary_text(audit())
    req_idx = out.find("Required:")
    auto_idx = out.find("Auto-install (apt):")
    opt_idx = out.find("Optional (manual):")
    assert req_idx >= 0 and auto_idx >= 0 and opt_idx >= 0
    assert req_idx < auto_idx < opt_idx, (
        f"tier order broken: req={req_idx} auto={auto_idx} opt={opt_idx}"
    )


def test_format_summary_marks_present_with_check():
    """A dep that resolves on $PATH must render with a check mark.
    Use sh as the canary."""
    deps = (DepSpec("sh", "dash", "POSIX shell", "required", ""),)
    statuses = audit(deps)
    out = format_summary_text(statuses)
    assert "✓ POSIX shell" in out
    assert "✗ POSIX shell" not in out


def test_format_summary_marks_missing_with_cross_and_feature_text():
    deps = (DepSpec("not-a-real-tool-zzz", "x", "FakeTool", "auto", "Foo disabled"),)
    statuses = audit(deps)
    out = format_summary_text(statuses)
    assert "✗ FakeTool" in out
    assert "Foo disabled" in out


def test_format_summary_includes_path_for_present_tools():
    """User audit motivation: surface WHERE meld is (or isn't) so the
    user knows whether the right binary was picked up."""
    deps = (DepSpec("sh", "dash", "POSIX shell", "required", ""),)
    statuses = audit(deps)
    out = format_summary_text(statuses)
    # /bin/sh or /usr/bin/sh — distro dependent
    assert "/bin/sh" in out


# ─── missing_features ──────────────────────────────────────────────────────


def test_missing_features_lists_only_auto_tier_failures():
    """Required missing → install fails before audit; tracked elsewhere.
    Optional missing → user opted out, no nag.
    Only 'auto' tier missing produces a feature-disabled line."""
    deps = (
        DepSpec("not-real-required-zzz", "x", "Req",  "required", ""),
        DepSpec("not-real-auto-zzz",     "x", "AutoTool", "auto", "Auto feature"),
        DepSpec("not-real-opt-zzz",      "x", "OptTool",  "optional", "Opt thing"),
    )
    statuses = audit(deps)
    miss = missing_features(statuses)
    assert miss == ["Auto feature"], (
        f"expected only auto-tier missing features, got {miss}"
    )


def test_missing_features_empty_when_everything_resolves():
    deps = (DepSpec("sh", "dash", "POSIX shell", "required", ""),)
    assert missing_features(audit(deps)) == []


def test_missing_features_skips_auto_tier_with_empty_feature_text():
    """Defensive: an 'auto' dep with feature='' shouldn't crash the
    list with a falsy entry."""
    deps = (DepSpec("not-real-zzz", "x", "X", "auto", ""),)
    statuses = audit(deps)
    miss = missing_features(statuses)
    assert miss == []
