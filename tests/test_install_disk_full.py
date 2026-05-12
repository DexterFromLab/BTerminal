"""Failure mode: disk full during install (#34 / #106, audit § 6.1 #7).

When `install.sh` hits ENOSPC mid-install (filesystem out of space),
the script must:

  1. Detect the failure via `set -e` + ERR trap — every disk-touching
     command that runs at top level (not inside `if`) propagates
     non-zero to the trap.
  2. Restore from BACKUP_DIR if populated → emit
     `BTERMINAL_ROLLBACK_OK`.
  3. Otherwise emit `BTERMINAL_FRESH_INSTALL_FAILED` with a
     diagnostic message pointing the user at the actual error.
  4. Exit non-zero so the GUI installer + scripted automation
     detect the failure.

Three decision branches mapped to actual install.sh code paths:
  (a) cp ENOSPC during file install — `cp -r "$SCRIPT_DIR/bterminal"
      "$INSTALL_DIR/bterminal"` (line 634) at top level under set -e.
      Failure → ERR trap fires → _on_error rollback.
  (b) npm install ENOSPC — wrapped in `if npm install ...` (line
      334-339), so failure does NOT trigger ERR. The script reports
      via `fail` helper but continues. Pin this softer behaviour.
  (c) curl|sh ollama install ENOSPC (audit doc says 'pipx install'
      but install.sh has no pipx — this is the curl|sh path at
      lines 451-460, also wrapped in `if`, also non-fatal).

Manual VM smoke (`mount -t tmpfs -o size=10M tmpfs /tmp/full && cd
/tmp/full && bash install.sh`) is documented in tests/manual/README.md.
Headless tests below pin the dispatch logic through bash subprocess
+ source-grep without needing tmpfs/sudo.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ─── set -e + ERR trap form the disk-full safety net ────────────────────


def test_install_sh_uses_set_e_for_ENOSPC_propagation():
    """`set -euo pipefail` at line 2 — every disk-touching command at
    top level propagates non-zero exit to the ERR trap. Without this,
    a failed `cp` would silently leave a half-installed state."""
    src = INSTALL_SH.read_text()
    first_lines = "\n".join(src.split("\n")[:5])
    assert "set -euo pipefail" in first_lines, (
        f"install.sh missing set -euo pipefail in first 5 lines: "
        f"{first_lines!r}"
    )


def test_install_sh_err_trap_fires_on_any_command_failure():
    """`trap '_on_error' ERR` ensures any non-zero exit (cp ENOSPC,
    curl exhaustion, dpkg space error) triggers the rollback path.
    Pin so a refactor that narrows the trap to specific signals
    breaks ENOSPC handling."""
    src = INSTALL_SH.read_text()
    assert "trap '_on_error' ERR" in src
    # And SIGINT/TERM (#33) — both in place
    assert "trap '_on_interrupt' INT TERM" in src


def test_on_error_handler_distinguishes_rollback_from_fresh_install():
    """_on_error has two branches:
      - BACKUP_DIR populated → restore + BTERMINAL_ROLLBACK_OK
      - No backup yet → BTERMINAL_FRESH_INSTALL_FAILED
    Both messages tell the user something actionable. Pin both
    branches."""
    src = INSTALL_SH.read_text()
    fn_start = src.find("_on_error() {")
    fn_end = src.find("\n}\n", fn_start)
    body = src[fn_start:fn_end + 2]
    # Both markers
    assert "BTERMINAL_ROLLBACK_OK" in body
    assert "BTERMINAL_FRESH_INSTALL_FAILED" in body
    # Both branches actionable
    assert "Previous version restored" in body
    assert "Fix the error above" in body


# ─── (a) cp ENOSPC at top level → ERR trap → rollback ───────────────────


def test_cp_failure_at_top_level_triggers_err_trap_with_backup(tmp_path):
    """Synthesize the 'cp ENOSPC after BACKUP_DIR populated' state
    by extracting install.sh's _on_error + traps + simulating a
    failing `cp` at top level. Verify that ERR trap fires and the
    rollback restore loop runs."""
    src = INSTALL_SH.read_text()
    # Extract _on_error definition + ERR trap line
    on_error_start = src.find("_on_error() {")
    err_trap_end = src.find("trap '_on_interrupt' INT TERM\n",
                             on_error_start)
    snippet = src[on_error_start:err_trap_end + len(
        "trap '_on_interrupt' INT TERM\n")]

    # Build state that mimics 'after backup, mid-files'
    backup_dir = tmp_path / "fake-backup"
    backup_dir.mkdir()
    (backup_dir / "ctx").write_text("#!/bin/bash\n# original ctx\n")
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "ctx").write_text("#!/bin/bash\n# CURRENT ctx\n")

    test_script = tmp_path / "cp_fail_test.sh"
    test_script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f'BACKUP_DIR="{backup_dir}"\n'
        f'INSTALL_DIR="{install_dir}"\n'
        'BTERMINAL_FILES=(ctx consult tasks claude_log memory_wizard)\n'
        f"{snippet}\n"
        # Simulate disk full: cp from /dev/null to a bogus path, OR
        # use `false` to model 'cp returned ENOSPC' uniformly.
        # (Real ENOSPC has the same observable: cp returns 28; bash
        # set -e treats any non-zero the same way.)
        "false  # simulates: cp -r ... → ENOSPC\n"
    )
    test_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(test_script)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    # Rollback marker emitted on stderr
    assert "BTERMINAL_ROLLBACK_OK" in result.stderr, (
        f"rollback marker missing — full stderr:\n{result.stderr}"
    )
    # File restored from backup
    restored = (install_dir / "ctx").read_text()
    assert "original ctx" in restored, (
        f"restore didn't run: {restored!r}"
    )
    # Backup cleaned up
    assert not backup_dir.exists()


def test_cp_failure_with_no_backup_emits_fresh_install_marker(tmp_path):
    """ENOSPC during phase 1 (before BACKUP_DIR populated):
    BACKUP_DIR is empty, _on_error falls through the no-backup
    branch → emits BTERMINAL_FRESH_INSTALL_FAILED."""
    src = INSTALL_SH.read_text()
    on_error_start = src.find("_on_error() {")
    err_trap_end = src.find("trap '_on_interrupt' INT TERM\n",
                             on_error_start)
    snippet = src[on_error_start:err_trap_end + len(
        "trap '_on_interrupt' INT TERM\n")]

    test_script = tmp_path / "fresh_fail.sh"
    test_script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'BACKUP_DIR=""\n'  # empty — pre-backup phase
        'INSTALL_DIR=""\n'
        'BTERMINAL_FILES=(ctx)\n'
        f"{snippet}\n"
        "false  # simulates ENOSPC before backup\n"
    )
    test_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(test_script)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "BTERMINAL_FRESH_INSTALL_FAILED" in result.stderr
    # No rollback marker (nothing to restore)
    assert "BTERMINAL_ROLLBACK_OK" not in result.stderr
    # User-facing diagnostic in stdout
    assert "Fix the error above" in result.stdout


def test_cp_in_install_sh_runs_at_top_level_under_set_e():
    """Source-grep: `cp -r "$SCRIPT_DIR/bterminal"` on line 634 is
    NOT inside an `if` block — set -e propagates ENOSPC to ERR trap.
    Pin the structural property: a refactor that wraps this in
    `if cp ... ; then` would silently accept disk-full failures."""
    src = INSTALL_SH.read_text()
    cp_idx = src.find('cp -r "$SCRIPT_DIR/bterminal"')
    assert cp_idx > 0
    # Look at the 80 chars BEFORE the cp call — must NOT have an
    # opening `if` clause that would suppress set -e.
    preceding = src[max(0, cp_idx - 200):cp_idx]
    last_line = preceding.split("\n")[-1].lstrip()
    assert not last_line.startswith("if "), (
        f"cp command wrapped in `if` block: {last_line!r}"
    )
    # And no `cp ... || true` suppression
    cp_line_end = src.find("\n", cp_idx)
    cp_line = src[cp_idx:cp_line_end]
    assert "|| true" not in cp_line, (
        f"cp suppresses ENOSPC with || true: {cp_line!r}"
    )


# ─── (b) npm install ENOSPC: explicit if-branch handling ────────────────


def test_npm_install_calls_wrapped_in_if_block_for_explicit_handling():
    """Source-grep: `npm install` calls are wrapped in `if npm
    install ...; then ... else ... fi`. This means:
      - Failure does NOT trigger ERR trap (the if-branch consumes
        the exit code).
      - Failure goes through the explicit `fail` helper which
        appends to ERRORS[] for the [SUMMARY] block.
    Pin so a refactor that drops the if-wrapper inadvertently
    promotes npm errors to fatal rollbacks."""
    src = INSTALL_SH.read_text()
    # Find every `npm install` call site
    lines = src.split("\n")
    npm_lines_with_context = []
    for i, line in enumerate(lines):
        if "npm install" in line and not line.lstrip().startswith("#"):
            # Look at this line + 2 lines before for the `if` keyword
            ctx = lines[max(0, i - 2):i + 1]
            ctx_text = "\n".join(ctx)
            npm_lines_with_context.append((i + 1, ctx_text))

    # At least one canonical 'if npm install -g @anthropic-ai/claude-code'
    canonical_present = False
    for lineno, ctx in npm_lines_with_context:
        if "@anthropic-ai/claude-code" in ctx and "if " in ctx:
            canonical_present = True
            break
    assert canonical_present, (
        f"no `if npm install ...claude-code` block found — disk-full "
        f"in npm install would now trigger fatal rollback. Lines: "
        f"{npm_lines_with_context}"
    )


def test_npm_install_failure_uses_soft_helper_not_err_trap():
    """The npm install error branch must use a SOFT helper (warn or
    fail) — both append to a report and continue — instead of letting
    `set -e` fire the ERR trap and roll back the whole install.

    Originally this test required `fail` specifically, but install.sh
    deliberately uses `warn` for npm provider installs (Claude/Copilot
    are --selected opt-ins; their failure shouldn't abort an otherwise
    successful BT install). Both helpers satisfy the contract.

    Pin: at least one npm-provider failure path must invoke warn() or
    fail() with a message mentioning the provider, AND must live inside
    an `if npm install ...; then ... else <soft>; fi` block."""
    src = INSTALL_SH.read_text()
    assert "fail() {" in src, "fail() helper missing"
    assert "warn() {" in src, "warn() helper missing"

    # Find an `if npm install ...` block and check its else-branch
    # contains warn/fail with a provider name.
    lines = src.split("\n")
    found_soft_handling = False
    for i, line in enumerate(lines):
        if "if npm install -g @anthropic-ai/claude-code" not in line:
            continue
        # Scan forward up to 30 lines for the matching `else` + soft helper
        block = "\n".join(lines[i:i + 30])
        if (("warn \"Claude Code installation failed" in block
             or "fail \"Claude Code installation failed" in block)
                and "else" in block):
            found_soft_handling = True
            break
    assert found_soft_handling, (
        "no `if npm install ... claude-code; ...; else warn/fail \"Claude "
        "Code installation failed\"; fi` block found — npm errors might "
        "now propagate to ERR trap and trigger fatal rollback."
    )


# ─── (c) curl|sh ollama install: same softer handling ──────────────────


def test_ollama_curl_install_wrapped_in_if_block():
    """Same softer handling as npm: the live `curl ollama.com/install.sh
    | sh` invocation is inside `if`, so ENOSPC during ollama download
    just warns (TOOL_REPORT entry as 'missing') without aborting.

    Find the live invocation (piped to sh) — NOT the string literals
    inside `add_manual_install` hints or `warn` messages that mention
    the same URL. The marker for a real invocation is `| ... sh`
    (pipe-to-sh) on or near the curl line. DOWNLOAD POLICY forbids
    `-s`/`-fsSL`, so the live call uses `curl -fL`."""
    src = INSTALL_SH.read_text()
    lines = src.split("\n")
    live_invocation_line = -1
    for i, line in enumerate(lines):
        if "ollama.com/install.sh" not in line:
            continue
        # Skip strings inside add_manual_install / warn / info hints
        # (those wrap the URL in double quotes that span the whole arg)
        stripped = line.strip()
        if stripped.startswith(("add_manual_install", "warn", "info", '"')):
            continue
        # The live invocation pipes to sh
        if "| stdbuf -oL sh" in line or "| sh" in line:
            live_invocation_line = i
            break
    assert live_invocation_line > 0, (
        "no live `curl ... ollama.com/install.sh | sh` invocation found; "
        "test cannot verify ENOSPC handling without the real call site"
    )
    # Walk backwards up to 5 lines looking for `if`
    window_start = max(0, live_invocation_line - 5)
    window = "\n".join(lines[window_start:live_invocation_line + 1])
    assert " if " in (" " + window) or window.lstrip().startswith("if "), (
        f"curl ollama install (line {live_invocation_line + 1}) not in "
        f"`if` block — ENOSPC during ollama download would trigger fatal "
        f"rollback. Context:\n{window}"
    )


def test_ollama_install_failure_warns_only_does_not_fail():
    """Pin: ollama install failure path uses `warn` (yellow ⚠)
    not `fail` (red ✗). Reason: ollama is opt-in via --selected
    llama; failures shouldn't abort an otherwise-successful BT
    install."""
    src = INSTALL_SH.read_text()
    # Look at the ollama failure branch (line 458 area)
    ollama_idx = src.find("Ollama install failed")
    assert ollama_idx > 0
    # The line containing this message uses warn(), not fail()
    line_start = src.rfind("\n", 0, ollama_idx) + 1
    line_end = src.find("\n", ollama_idx)
    line = src[line_start:line_end]
    assert "warn" in line.lower(), (
        f"ollama failure line uses fail() instead of warn(): {line!r}"
    )


# ─── Cross-cutting: error message contracts ─────────────────────────────


def test_err_trap_messages_clearly_indicate_failure_to_user(tmp_path):
    """When ENOSPC fires the ERR trap with no backup, the user sees
    a clear 'Installation failed' banner + 'Fix the error above'
    hint. Without it, the failure is just a non-zero exit — bad UX."""
    src = INSTALL_SH.read_text()
    on_error_start = src.find("_on_error() {")
    on_error_end = src.find("\n}\n", on_error_start)
    body = src[on_error_start:on_error_end + 2]
    assert "Installation failed" in body
    # Either branch has actionable text
    assert "Fix the error above" in body or \
        "Run ./install.sh again" in body or \
        "Previous version restored" in body


def test_rollback_loop_uses_2_dev_null_so_missing_files_dont_fail(tmp_path):
    """The rollback loop has `2>/dev/null || true` after each cp —
    pin so a refactor that drops this suppression doesn't make the
    rollback ITSELF fail when a backup file went missing during a
    second-stage failure."""
    src = INSTALL_SH.read_text()
    # The canonical line in _on_error
    on_error_start = src.find("_on_error() {")
    on_error_end = src.find("\n}\n", on_error_start)
    body = src[on_error_start:on_error_end + 2]
    # The cp call in the restore loop has '2>/dev/null || true'
    assert "2>/dev/null || true" in body, (
        "rollback cp lost the 2>/dev/null || true suppression — "
        "second-stage failures would propagate"
    )


# ─── ENOSPC simulated with a fake `cp` shim on PATH ─────────────────────


def test_simulated_enospc_via_fake_cp_shim_triggers_rollback(tmp_path):
    """End-to-end with a fake `cp` that returns ENOSPC. This is the
    most realistic headless reproduction of the disk-full scenario:
    a real ENOSPC, just from a bogus binary instead of a real tmpfs."""
    src = INSTALL_SH.read_text()
    on_error_start = src.find("_on_error() {")
    err_trap_end = src.find("trap '_on_interrupt' INT TERM\n",
                             on_error_start)
    snippet = src[on_error_start:err_trap_end + len(
        "trap '_on_interrupt' INT TERM\n")]

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "ctx").write_text("# orig\n")
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "ctx").write_text("# current\n")

    # Fake cp shim: prints ENOSPC error, exits 28 (Linux ENOSPC)
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    fake_cp = fake_bin / "cp"
    fake_cp.write_text(
        "#!/bin/bash\n"
        'echo "cp: error writing: No space left on device" >&2\n'
        "exit 28\n"
    )
    fake_cp.chmod(0o755)

    test_script = tmp_path / "enospc_repro.sh"
    test_script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f'BACKUP_DIR="{backup_dir}"\n'
        f'INSTALL_DIR="{install_dir}"\n'
        'BTERMINAL_FILES=(ctx)\n'
        f"{snippet}\n"
        # Force this script's `cp` to be the fake shim.
        # (The _on_error handler uses /usr/bin/cp via PATH lookup,
        # but when we call cp at top level here, the fake shim
        # comes first.)
        f'PATH="{fake_bin}:$PATH"\n'
        # Simulated disk-touching command — represents
        # `cp -r "$SCRIPT_DIR/bterminal" "$INSTALL_DIR/bterminal"`
        'cp /etc/passwd /tmp/will-not-be-written\n'
    )
    test_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(test_script)],
        capture_output=True, text=True, timeout=10,
    )
    # ENOSPC propagated, exit 28 from fake cp
    assert result.returncode != 0
    # User saw the actual disk-full message
    assert "No space left on device" in result.stderr
    # Rollback fired (BACKUP_DIR was populated)
    assert "BTERMINAL_ROLLBACK_OK" in result.stderr


# ─── Final cleanup happens only on the success path ─────────────────────


def test_final_backup_cleanup_runs_only_on_success():
    """After successful install, install.sh has a final
    `[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"`
    that frees /tmp space. On ENOSPC failure, the _on_error handler
    runs `rm -rf "$BACKUP_DIR"` AFTER the restore loop — pin both
    paths so /tmp doesn't accumulate orphan backups."""
    src = INSTALL_SH.read_text()
    # Final cleanup on success path
    final_cleanup = ('[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]'
                      ' && rm -rf "$BACKUP_DIR"')
    assert final_cleanup in src, (
        "missing final BACKUP_DIR cleanup on success — orphan dirs "
        "accumulate"
    )
    # _on_error also cleans up
    on_error_start = src.find("_on_error() {")
    on_error_end = src.find("\n}\n", on_error_start)
    body = src[on_error_start:on_error_end + 2]
    assert 'rm -rf "$BACKUP_DIR"' in body, (
        "_on_error doesn't clean BACKUP_DIR — ENOSPC + repeat "
        "install would pile up /tmp"
    )


# ─── Cross-reference: rollback marker contract from #62 ────────────────


def test_rollback_marker_unchanged_for_gui_compat():
    """The `BTERMINAL_ROLLBACK_OK` marker is consumed by the
    BTerminal GUI updater (#52 dialog flow) to show a friendly
    rollback message. Pin the literal so a typo here doesn't
    silently break that integration."""
    src = INSTALL_SH.read_text()
    assert src.count("BTERMINAL_ROLLBACK_OK") >= 1
    # NOT BTERMINAL_ROLLBACKOK or similar typo variants
    assert "BTERMINAL_ROLLBACK" in src
    # Specifically the OK variant
    assert "BTERMINAL_ROLLBACK_OK" in src


def test_fresh_install_failed_marker_present_for_diagnostic():
    """Companion to ROLLBACK_OK — emitted when ENOSPC happens
    BEFORE backup is populated. Pin literal."""
    src = INSTALL_SH.read_text()
    assert src.count("BTERMINAL_FRESH_INSTALL_FAILED") >= 1
