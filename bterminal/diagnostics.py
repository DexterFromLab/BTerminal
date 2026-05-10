"""bterminal.diagnostics — installer + runtime dependency audit (task #62).

Canonical dependency registry consumed by:
  - install.sh (mirror, kept in sync via tools/test_install_summary.sh)
  - Tools → Diagnostics… menu (live status dialog)
  - tests/test_diagnostics.py

Each entry declares whether a missing tool blocks a feature
(tier="auto" — installer tries apt install, BTerminal degrades a
specific feature when missing) or is a true UX nice-to-have
(tier="optional" — pure convenience). Required tools (tier="required")
are install-time hard blockers; missing them aborts install.sh.

Bug history (2026-05-07): user reported uncertainty whether meld was
installed because install.sh "auto" tier only logged a one-line warning
on apt failure. Now every tool reports its post-install status in a
[SUMMARY] block + Help menu live audit.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class DepSpec:
    """One dependency in the registry.

    cmd:     command name resolved via shutil.which()
    apt_pkg: Debian/Ubuntu package name (informational; install.sh uses it)
    label:   human-readable name in the summary block
    tier:    'required' | 'auto' | 'optional'
              required: install aborts when missing
              auto:     install tries apt install; missing degrades a specific feature
              optional: pure UX nice-to-have, never installed automatically
    feature: short text describing what stops working when this dep is missing.
             Empty string for 'required' deps (install just aborts).
    """

    cmd: str
    apt_pkg: str
    label: str
    tier: str
    feature: str = ""


# Canonical registry — single source of truth. install.sh's check_tool
# call list (lines 272-280) MUST stay in sync with the cmd column here;
# tools/test_install_summary.sh asserts the parity.
DEPENDENCIES: tuple[DepSpec, ...] = (
    DepSpec("git",      "git",                "git",                            "required", ""),
    DepSpec("ssh",      "openssh-client",     "ssh",                            "required", ""),
    DepSpec("git-lfs",  "git-lfs",            "git-lfs",                        "optional",
            "large-file storage in repos (rare; safe to skip)"),
    DepSpec("xdg-open", "xdg-utils",          "xdg-open",                       "optional",
            "'Open with…' system handlers in CTX wizard"),
    DepSpec("meld",     "meld",               "meld",                           "auto",
            "diff-merge in CTX wizard disabled"),
    DepSpec("pdflatex", "texlive-latex-extra", "pdflatex (LaTeX)",              "auto",
            "LaTeX → PDF compilation in editor disabled"),
    DepSpec("latexmk",  "latexmk",            "latexmk",                        "auto",
            "LaTeX build automation disabled"),
    DepSpec("pdftoppm", "poppler-utils",      "poppler-utils (PDF preview)",    "auto",
            "PDF preview pane in editor disabled"),
    DepSpec("pandoc",   "pandoc",             "pandoc",                         "auto",
            "markdown ⇄ docx/odt conversion disabled"),
)
# Task #1 (#73): psutil intentionally NOT in DEPENDENCIES — DepSpec is
# for CLI tools resolved via shutil.which. psutil is a Python library
# with a soft-import fallback to /proc/meminfo in system_probe.py, so
# it doesn't fit the registry abstraction. Document elsewhere if needed.


@dataclass
class DepStatus:
    """Result of detect_tool() for one dependency."""

    spec: DepSpec
    present: bool
    path: Optional[str] = None
    version: Optional[str] = None


def detect_tool(spec: DepSpec, version_arg: str = "--version",
                timeout: float = 2.0) -> DepStatus:
    """Live presence + version probe for one dep. Pure (read-only).

    Uses shutil.which (resolves $PATH the way the user's shell does)
    and a short-timeout `<cmd> --version` for the version string —
    swallowed on any failure so a hung binary doesn't freeze the
    Diagnostics dialog.
    """
    path = shutil.which(spec.cmd)
    if not path:
        return DepStatus(spec=spec, present=False)
    version = None
    try:
        result = subprocess.run(
            [spec.cmd, version_arg],
            capture_output=True, text=True, timeout=timeout,
        )
        # Prefer first non-empty stdout line (most CLIs); fall back to stderr.
        first = next(
            (l.strip() for l in (result.stdout or result.stderr or "").splitlines()
             if l.strip()),
            None,
        )
        if first:
            version = first[:120]  # cap so we don't blow up the dialog
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass
    return DepStatus(spec=spec, present=True, path=path, version=version)


def audit(deps: tuple[DepSpec, ...] = DEPENDENCIES) -> list[DepStatus]:
    """Run detect_tool against every dep in the registry."""
    return [detect_tool(d) for d in deps]


# ─── #109: AI provider audit ───────────────────────────────────────────────


@dataclass(frozen=True)
class AIProviderStatus:
    """Audit result for one AI CLI provider (claude/copilot/aider).

    name:    canonical provider name ('claude' | 'copilot' | 'aider')
    label:   human-readable display name
    present: True iff binary resolves on PATH or known fallback locations
    path:    absolute path to binary, or "" when missing
    version: short --version output (max 120 chars), or "" when probe fails
    """
    name: str
    label: str
    present: bool = False
    path: str = ""
    version: str = ""


# Canonical fallback locations for each provider — mirrors install.sh's
# find_claude_bin / find_copilot_bin lookup chain so users get the same
# detection result whether they probe via install.sh, BT runtime, or
# this Diagnostics audit.
_AI_PROVIDER_LOCATIONS: dict[str, list[str]] = {
    "claude": [
        "~/.local/bin/claude",
        "~/.npm-global/bin/claude",
        "/usr/local/bin/claude",
        "/usr/bin/claude",
    ],
    "copilot": [
        "~/.local/bin/copilot",
        "~/.npm-global/bin/copilot",
        "/usr/local/bin/copilot",
        "/usr/bin/copilot",
    ],
    "aider": [
        "~/.local/bin/aider",
        "/usr/local/bin/aider",
        "/usr/bin/aider",
    ],
}

_AI_PROVIDER_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "copilot": "GitHub Copilot CLI",
    "aider": "Aider (local LLM)",
}


def _detect_ai_provider(name: str) -> AIProviderStatus:
    """Resolve one AI provider's binary + version. Returns
    AIProviderStatus(present=False, path="", version="") when missing."""
    import os as _os
    label = _AI_PROVIDER_LABELS.get(name, name)
    candidates = _AI_PROVIDER_LOCATIONS.get(name, [])
    bin_path = ""
    for cand in candidates:
        expanded = _os.path.expanduser(cand)
        if _os.path.isfile(expanded) and _os.access(expanded, _os.X_OK):
            bin_path = expanded
            break
    if not bin_path:
        # Fall back to PATH lookup (covers user-managed installs not in
        # the canonical chain above).
        which_result = shutil.which(name)
        if which_result:
            bin_path = which_result
    if not bin_path:
        return AIProviderStatus(name=name, label=label, present=False)
    # Probe --version with a short timeout.
    version = ""
    try:
        proc = subprocess.run(
            [bin_path, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        first_line = (proc.stdout or proc.stderr or "").strip().splitlines()
        if first_line:
            version = first_line[0][:120]
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass
    return AIProviderStatus(
        name=name, label=label, present=True,
        path=bin_path, version=version,
    )


def audit_ai_providers(
    names: tuple[str, ...] = ("claude", "copilot", "aider"),
) -> list[AIProviderStatus]:
    """Probe every AI provider in `names`, return list of statuses.

    Used by format_summary_text() to append an 'AI Providers:' section
    to the Tools → Diagnostics dialog body. Mirrors install.sh's
    find_*_bin lookup chain so dialog shows the same provider status
    that install.sh's [SUMMARY] block reports."""
    return [_detect_ai_provider(n) for n in names]


def format_summary_text(
    statuses: list[DepStatus],
    ai_statuses: Optional[list[AIProviderStatus]] = None,
) -> str:
    """Human-readable [SUMMARY] block — same format used by install.sh
    end-of-install report and the Diagnostics dialog body.

    Layout (newline-separated):
        [SUMMARY]
        Required:
          ✓ git           (/usr/bin/git, git version 2.43.0)
          ✓ ssh           (/usr/bin/ssh, OpenSSH_9.6p1)
        Auto-install (apt):
          ✓ meld          (/usr/bin/meld, meld 3.22.0)
          ✗ pandoc       — markdown ⇄ docx/odt conversion disabled
        Optional (manual):
          ✓ git-lfs       (...)
          ✗ xdg-open     — 'Open with…' system handlers in CTX wizard
        AI Providers:                              (#109 — opt-in)
          ✓ claude        (~/.local/bin/claude, 2.1.136 …)
          ✓ copilot       (~/.local/bin/copilot, GitHub Copilot CLI 1.0.44)
          ✗ aider         — not installed (pipx install aider-chat)

    Sorted by tier (required → auto → optional → AI providers).
    `ai_statuses=None` skips the AI Providers section (legacy behavior);
    pass `audit_ai_providers()` to include it.
    """
    lines = ["[SUMMARY]"]
    by_tier = {"required": [], "auto": [], "optional": []}
    for s in statuses:
        by_tier.setdefault(s.spec.tier, []).append(s)

    tier_titles = (
        ("required", "Required"),
        ("auto",     "Auto-install (apt)"),
        ("optional", "Optional (manual)"),
    )
    for tier, title in tier_titles:
        rows = by_tier.get(tier, [])
        if not rows:
            continue
        lines.append(f"{title}:")
        for st in sorted(rows, key=lambda s: s.spec.label):
            mark = "✓" if st.present else "✗"
            label = st.spec.label.ljust(28)
            if st.present:
                v = f", {st.version}" if st.version else ""
                lines.append(f"  {mark} {label} ({st.path}{v})")
            else:
                feature = st.spec.feature or "missing — install required"
                lines.append(f"  {mark} {label} — {feature}")

    # #109: AI provider audit section — opt-in via ai_statuses arg.
    if ai_statuses:
        lines.append("AI Providers:")
        # Standard install hints for missing providers
        install_hints = {
            "claude":  "npm install -g @anthropic-ai/claude-code",
            "copilot": "npm install -g @github/copilot",
            "aider":   "pipx install aider-chat",
        }
        for ai in ai_statuses:
            mark = "✓" if ai.present else "✗"
            label = ai.label.ljust(28)
            if ai.present:
                v = f", {ai.version}" if ai.version else ""
                lines.append(f"  {mark} {label} ({ai.path}{v})")
            else:
                hint = install_hints.get(ai.name, "")
                msg = f"not installed ({hint})" if hint else "not installed"
                lines.append(f"  {mark} {label} — {msg}")

    return "\n".join(lines)


# ─── Task #8 (#80): centralized feature gating ─────────────────────────────


import time as _time_mod


# {cmd: (timestamp, present_bool)} — populated lazily by
# is_feature_available; cleared by invalidate_cache().
_FEATURE_CACHE: dict[str, tuple[float, bool]] = {}

# Subscribers notified when invalidate_cache() runs (#9 / #81 hook).
_INVALIDATION_LISTENERS: list = []


def is_feature_available(cmd: str, ttl_sec: float = 60.0) -> bool:
    """Cached check: is `cmd` available on $PATH?

    Replaces ad-hoc `shutil.which("meld")` in 8+ UI callsites (audit § 3).
    The TTL cache amortizes the cost across rapid `refresh()` cycles —
    e.g. opening Files panel doesn't re-probe meld for every tab open.

    `cmd` is the binary name (`meld`, `xdg-open`). When `cmd` matches a
    DepSpec.cmd in DEPENDENCIES, full version probe runs. For arbitrary
    commands not in registry, falls back to plain shutil.which.

    `ttl_sec=0` disables caching (always re-probe; useful in tests).

    Returns True iff the binary resolves at least once via shutil.which
    (DepSpec full probe also checks that file is executable).
    """
    now = _time_mod.monotonic()
    cached = _FEATURE_CACHE.get(cmd)
    if cached is not None and ttl_sec > 0:
        ts, present = cached
        if now - ts < ttl_sec:
            return present

    # Fresh probe — prefer DepSpec full check when registered, else
    # fall through to bare shutil.which.
    spec = next((d for d in DEPENDENCIES if d.cmd == cmd), None)
    if spec is not None:
        present = detect_tool(spec).present
    else:
        import shutil as _sh
        present = _sh.which(cmd) is not None

    _FEATURE_CACHE[cmd] = (now, present)
    return present


def invalidate_cache() -> None:
    """Drop all cached feature checks. Called after install completes
    (InstallerWizard #77 page 5 → emit deps-changed) so UI panels
    re-probe instead of showing stale 'meld missing' state.

    Notifies registered listeners so panels can call their refresh()."""
    _FEATURE_CACHE.clear()
    for listener in list(_INVALIDATION_LISTENERS):
        try:
            listener()
        except Exception:
            # Don't let one bad listener block others / crash the wizard.
            pass


def subscribe_invalidation(listener) -> None:
    """Register a zero-arg callable to be invoked after invalidate_cache().
    Used by panels (Files, sidebar context menu, etc.) to refresh their
    sensitivity state when deps change."""
    if listener not in _INVALIDATION_LISTENERS:
        _INVALIDATION_LISTENERS.append(listener)


def unsubscribe_invalidation(listener) -> None:
    """Remove a previously-subscribed listener. Tests use this to keep
    state isolated; panels rarely need it (singletons)."""
    if listener in _INVALIDATION_LISTENERS:
        _INVALIDATION_LISTENERS.remove(listener)


def missing_features(statuses: list[DepStatus]) -> list[str]:
    """Return a flat list of feature-blocker descriptions (tier='auto'
    deps that aren't present). Empty list when everything is fine.

    Used by the Diagnostics dialog header to give a one-line
    "you're missing N features" summary before the full table.
    """
    return [
        s.spec.feature
        for s in statuses
        if not s.present and s.spec.tier == "auto" and s.spec.feature
    ]


__all__ = [
    "DEPENDENCIES",
    "DepSpec",
    "DepStatus",
    "audit",
    "detect_tool",
    "format_summary_text",
    "missing_features",
    # Task #8 (#80)
    "is_feature_available",
    "invalidate_cache",
    "subscribe_invalidation",
    "unsubscribe_invalidation",
]
