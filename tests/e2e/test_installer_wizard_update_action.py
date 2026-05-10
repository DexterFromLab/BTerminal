"""E2E test for BUG#16 — installer wizard has no Update action for
already-installed BTerminal whose disk files are out of sync with
repo HEAD.

Pre-fix: detect_install_state returned only "installed" / "broken" /
"not_installed". Wizard's welcome page offered Install (greyed when
installed), Fix, Uninstall. A user with v1.3.0 on disk + v1.3.1 in
repo had NO way to refresh through the GUI — they had to know about
`./install.sh` from the CLI or click Tools→Check for updates and hit
the in-app updater (which itself had BUG#15 broken).

Post-fix:
  - detect_install_state distinguishes "installed" (current) from
    "installed_outdated" (repo __main__.py SHA-256 differs from
    installed copy). Cheap proxy — __main__.py touches imports on
    every release, so SHA mismatch ≈ "user has older files".
  - Wizard welcome page exposes 4 radios (Install / Update / Fix /
    Uninstall), with Update enabled + pre-selected when state is
    "installed_outdated".
  - build_install_argv accepts action="update" → maps to plain
    install.sh (no --fix flag). Phase [5/7] always rsyncs the
    bterminal/ package, so a normal install equals an update.
  - PAGES_BY_ACTION + HEADERS_BY_ACTION include the "update"
    flow (welcome → progress → summary, same as fix shape).

The test pins:
  - detect_install_state returns "installed_outdated" when SHA-256
    of repo's __main__.py differs from installed copy
  - same function returns "installed" when content matches
  - build_install_argv treats action="update" like "install"
    (no --fix / --uninstall flags)
  - wizard source has Update radio + action_radios['update']
  - PAGES_BY_ACTION + HEADERS_BY_ACTION + label maps include "update"
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WIZARD_PY = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"


# ── detect_install_state — outdated detection ────────────────────────────


def _fake_install_tree(tmp: Path, main_py_content: bytes) -> Path:
    """Build a fake ~/.local/share/bterminal layout that passes the
    other detect_install_state checks (launcher, package init,
    companion CLI symlinks) so only the version diff matters."""
    home = tmp / "home"
    home.mkdir()
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    share = home / ".local" / "share" / "bterminal"
    (share / "bterminal").mkdir(parents=True)
    # Launcher (file is fine, doesn't have to be a real shell script
    # for detect_install_state — only existence matters)
    (bin_dir / "bterminal").write_text("#!/bin/bash\n")
    # Package init + __main__.py
    (share / "bterminal" / "__init__.py").write_text("")
    (share / "bterminal" / "__main__.py").write_bytes(main_py_content)
    # Companion CLI symlinks (5 expected)
    for tool in ("ctx", "tasks", "consult",
                 "memory_wizard", "claude_log"):
        (bin_dir / tool).write_text("#!/bin/bash\n")
    return home


def test_detect_install_state_returns_outdated_on_sha_mismatch(tmp_path):
    """Pin: when installed __main__.py content differs from repo's
    copy, state is 'installed_outdated' — wizard can then offer
    Update."""
    from bterminal.ui.installer_wizard import detect_install_state

    repo = tmp_path / "repo"
    (repo / "bterminal").mkdir(parents=True)
    (repo / "install.sh").write_text("#!/bin/bash\necho ok\n")
    repo_main_content = b"# repo v1.3.1\nfrom bterminal import x\n"
    (repo / "bterminal" / "__main__.py").write_bytes(repo_main_content)

    home = _fake_install_tree(tmp_path, b"# installed v1.3.0\n")

    state = detect_install_state(home=home, repo_dir=repo)
    assert state == "installed_outdated", (
        f"expected 'installed_outdated' on SHA mismatch, got {state!r}"
    )


def test_detect_install_state_returns_installed_when_content_matches(tmp_path):
    """Pin (inverse): when installed __main__.py SHA matches repo's,
    state is 'installed' (no update needed)."""
    from bterminal.ui.installer_wizard import detect_install_state

    repo = tmp_path / "repo"
    (repo / "bterminal").mkdir(parents=True)
    (repo / "install.sh").write_text("#!/bin/bash\necho ok\n")
    same_content = b"# same content both sides\nimport bterminal\n"
    (repo / "bterminal" / "__main__.py").write_bytes(same_content)

    home = _fake_install_tree(tmp_path, same_content)

    state = detect_install_state(home=home, repo_dir=repo)
    assert state == "installed", (
        f"expected 'installed' when contents match, got {state!r}"
    )


def test_detect_install_state_without_repo_dir_falls_back_to_installed(tmp_path):
    """Pin: if caller doesn't supply repo_dir and we can't auto-detect,
    fall through to 'installed' (don't crash, don't claim outdated)."""
    from bterminal.ui.installer_wizard import detect_install_state

    home = _fake_install_tree(tmp_path, b"# whatever\n")
    # Pass a bogus repo dir that has no __main__.py
    bogus_repo = tmp_path / "bogus_repo"
    bogus_repo.mkdir()
    state = detect_install_state(home=home, repo_dir=bogus_repo)
    assert state == "installed", (
        f"missing repo_dir/__main__.py should fall through to "
        f"'installed', got {state!r}"
    )


# ── build_install_argv — action="update" ─────────────────────────────────


def test_build_install_argv_treats_update_like_install():
    """Pin: action='update' produces install.sh argv WITHOUT --fix
    or --uninstall. That way phase [5/7] always rsyncs new files
    (= an update is just an install on an existing setup)."""
    from bterminal.ui.installer_wizard import build_install_argv

    argv = build_install_argv("/repo", [], action="update")
    assert "--fix" not in argv, "update must NOT pass --fix"
    assert "--uninstall" not in argv, "update must NOT pass --uninstall"
    assert "--headless" in argv and "--status-json" in argv


def test_build_install_argv_update_propagates_no_sudo():
    """Pin: --no-sudo flag still honored under action=update."""
    from bterminal.ui.installer_wizard import build_install_argv

    argv = build_install_argv("/repo", [], action="update", no_sudo=True)
    assert "--no-sudo" in argv


def test_build_install_argv_update_passes_selected_deps():
    """Pin: --selected list is forwarded for update too (deps may
    need to upgrade alongside BT package files)."""
    from bterminal.ui.installer_wizard import build_install_argv

    argv = build_install_argv(
        "/repo", ["claude", "copilot"], action="update")
    assert "--selected" in argv
    idx = argv.index("--selected")
    assert argv[idx + 1] == "claude,copilot"


# ── Wizard source-level — Update radio + page sequence wired ────────────


def test_wizard_source_declares_update_action_radio():
    """Pin: welcome page builds a 4th radio for the update action and
    stores it in self._action_radios['update']."""
    src = WIZARD_PY.read_text(encoding="utf-8")
    assert 'self._action_radios["update"]' in src, (
        "wizard does not register an 'update' action radio"
    )
    assert "Update BTerminal" in src, (
        "no 'Update BTerminal' label string in wizard source"
    )


def test_wizard_default_action_is_update_when_outdated():
    """Pin: when state == 'installed_outdated' the wizard pre-selects
    the Update radio (rather than Fix or Install)."""
    src = WIZARD_PY.read_text(encoding="utf-8")
    # Look for the default-selection block
    pat = re.compile(
        r'if\s+self\._install_state\s*==\s*"installed_outdated":\s*\n'
        r'\s*rb_update\.set_active\(True\)\s*\n'
        r'\s*self\._action\s*=\s*"update"',
        re.MULTILINE,
    )
    assert pat.search(src), (
        "expected `if self._install_state == 'installed_outdated': "
        "rb_update.set_active(True); self._action = 'update'` block"
    )


def test_wizard_pages_by_action_has_update_sequence():
    """Pin: PAGES_BY_ACTION dict has an 'update' entry. Same page
    flow as 'fix' (welcome → progress → summary)."""
    from bterminal.ui.installer_wizard import InstallerWizard
    assert "update" in InstallerWizard.PAGES_BY_ACTION, (
        "PAGES_BY_ACTION missing 'update' key"
    )
    seq = InstallerWizard.PAGES_BY_ACTION["update"]
    # Same shape as fix — welcome (0), progress (4), summary (5)
    assert 0 in seq and 4 in seq and 5 in seq, (
        f"update sequence {seq} missing welcome/progress/summary"
    )


def test_wizard_headers_by_action_has_update_strings():
    """Pin: HEADERS_BY_ACTION has 'update' entry so user sees update-
    specific step labels rather than 'Step X of Y: …' fallback."""
    from bterminal.ui.installer_wizard import InstallerWizard
    assert "update" in InstallerWizard.HEADERS_BY_ACTION, (
        "HEADERS_BY_ACTION missing 'update' key"
    )
    headers = InstallerWizard.HEADERS_BY_ACTION["update"]
    # Each page in the sequence should have a header
    for page_idx in InstallerWizard.PAGES_BY_ACTION["update"]:
        assert page_idx in headers, (
            f"page {page_idx} has no update-specific header"
        )
    # At least the welcome (0) and progress (4) pages must mention
    # update so the user sees they're in the update flow. Summary
    # (5) is generic across actions — allowed to be just "Summary".
    welcome = headers[0].lower()
    progress = headers[4].lower()
    assert "updat" in welcome, (
        f"welcome header for update flow lacks 'updat' stem: "
        f"{headers[0]!r}"
    )
    assert "updat" in progress, (
        f"progress header for update flow lacks 'updat' stem: "
        f"{headers[4]!r}"
    )


def test_wizard_primary_button_label_for_update():
    """Pin: the Next → button on welcome page shows 'Update →' when
    action is 'update' (rather than 'Install →' or 'Repair →')."""
    src = WIZARD_PY.read_text(encoding="utf-8")
    # Match the dict that maps action → button label
    pat = re.compile(
        r'"install":\s*"Install →"\s*,\s*\n'
        r'\s*"update":\s*"Update →"\s*,',
        re.MULTILINE,
    )
    assert pat.search(src), (
        "primary-button label dict missing 'update': 'Update →' entry"
    )


# ── BUG#18: outdated state hides Install/Fix/Uninstall radios ───────────


def test_outdated_state_hides_non_update_radios():
    """Pin BUG#18: when state == 'installed_outdated' the wizard
    welcome page hides Install, Fix and Uninstall radios. User
    sees only the Update option — clear single-path decision."""
    src = WIZARD_PY.read_text(encoding="utf-8")
    # Look for the explicit hide() block we added
    pat = re.compile(
        r'if\s+self\._install_state\s*==\s*"installed_outdated":\s*\n'
        r'\s*rb_install\.hide\(\).*?'
        r'rb_fix\.hide\(\).*?'
        r'rb_uninstall\.hide\(\)',
        re.DOTALL,
    )
    assert pat.search(src), (
        "expected rb_install.hide() + rb_fix.hide() + rb_uninstall.hide() "
        "block guarded by `if self._install_state == 'installed_outdated':`. "
        "Without it, the wizard shows decision-fatigue radios alongside "
        "Update."
    )
    # set_no_show_all is required so show_all() on the dialog doesn't
    # un-hide them. Without this guard, Gtk re-shows hidden widgets.
    no_show_all = re.findall(
        r'rb_(install|fix|uninstall)\.set_no_show_all\(True\)', src)
    assert len(no_show_all) >= 3, (
        f"expected set_no_show_all(True) on all 3 hidden radios; "
        f"found {len(no_show_all)}: {no_show_all}"
    )
