"""Installer stub-binary detection (regression for 2026-05-08
`claude.exe` postinstall stub bug).

Reproduces: a previously-failed `npm install -g
@anthropic-ai/claude-code` left a placeholder file at
`~/.npm-global/lib/node_modules/.../bin/claude.exe` containing
literal `echo "Error: claude native binary not installed."`,
without +x bit. install.sh's `find_claude_bin` (`[[ -x ]]`
gate) skipped it AND didn't overwrite it. Result: BTerminal
spawn showed "Claude Code not found" even though a symlink
existed.

These tests pin the validate_npm_cli helper + ensure install.sh
treats the 4 known broken-binary states (missing, not-+x, stub,
--version-fail) as fail-closed, with actionable diagnostics.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ─── Source pins ───────────────────────────────────────────────────


def test_install_sh_has_validate_npm_cli_helper():
    """Pin: validate_npm_cli function exists in install.sh.
    Removing it would silently regress to the pre-fix
    behaviour where stub binaries were reported as ok."""
    src = INSTALL_SH.read_text()
    assert "validate_npm_cli()" in src
    assert "validate_explain()" in src


def test_validate_npm_cli_checks_executable_bit():
    """Pin: helper rejects file without +x (return code 2)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("validate_npm_cli() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "[[ ! -x " in body
    assert "return 2" in body


def test_validate_npm_cli_checks_for_stub_markers():
    """Pin: helper greps the file content for known stub
    error messages emitted by failed npm postinstall."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("validate_npm_cli() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    # The 3 sentinels we observed in the real-world failure
    assert "native binary not installed" in body
    assert "postinstall did not run" in body
    assert "optional dependency was not downloaded" in body
    assert "return 3" in body


def test_validate_npm_cli_runs_version_for_functional_check():
    """Pin: --version sanity check (return 4 on failure)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("validate_npm_cli() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "--version" in body
    assert "return 4" in body


def test_validate_explain_covers_all_4_failure_codes():
    """Pin: every return code from validate_npm_cli has a
    user-facing diagnostic. Missing entries would default to
    'unknown validation failure' which is unhelpful."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("validate_explain() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    for code in ("1)", "2)", "3)", "4)"):
        assert code in body


def test_install_sh_uses_validate_in_claude_install_branch():
    """Pin: install.sh's claude install branch gates on
    validate_npm_cli result. Skipping it would re-introduce
    the bug where stub was reported as 'installed'."""
    src = INSTALL_SH.read_text()
    # Both the update-existing branch AND the fresh-install
    # branch must call the validator
    claude_section = src[src.find("[2/7] Claude Code"):
                         src.find("[2.5/7]")]
    validate_calls = claude_section.count('validate_npm_cli "$EXISTING_CLAUDE"')
    assert validate_calls >= 3, (
        f"Claude section has only {validate_calls} validate_npm_cli "
        f"calls — need ≥3 (initial check, post-update re-check, "
        f"post-install re-check)"
    )


def test_install_sh_uses_validate_in_copilot_install_branch():
    """Same for Copilot."""
    src = INSTALL_SH.read_text()
    copilot_section = src[src.find("[2.5/7] Checking GitHub Copilot"):
                          src.find("[2.7/7]")]
    validate_calls = copilot_section.count('validate_npm_cli "$EXISTING_COPILOT"')
    assert validate_calls >= 3


def test_install_sh_clears_broken_stub_before_retry():
    """Pin: if existing claude is broken, install.sh removes
    the stub before re-running npm install. Without this,
    npm 'reinstall' may not overwrite a non-+x file."""
    src = INSTALL_SH.read_text()
    # Section guards stub cleanup with rm -f
    install_branch = src[src.find('"$CLAUDE_NEEDS_INSTALL" == true'):
                         src.find("[2.5/7] GitHub Copilot")]
    assert 'rm -f "$NPM_PREFIX/bin/claude"' in install_branch
    assert 'rm -rf "$NPM_PREFIX/lib/node_modules/@anthropic-ai"' in install_branch


def test_install_sh_retries_with_include_optional_flag():
    """Pin: install.sh retries npm install with
    `--include=optional --foreground-scripts` when the
    standard install fails. This is the documented npm
    workaround for postinstall not pulling native binaries."""
    src = INSTALL_SH.read_text()
    assert "--include=optional --foreground-scripts" in src


def test_install_sh_writes_structured_install_log():
    """Pin: install.sh writes timestamped log lines to
    `$CONFIG_DIR/install.log` for tail-able CI assertions."""
    src = INSTALL_SH.read_text()
    assert 'INSTALL_LOG="$CONFIG_DIR/install.log"' in src
    assert "log_line()" in src
    # ok/warn/fail/info hooks invoke log_line
    assert 'log_line "OK"' in src
    assert 'log_line "WARN"' in src
    assert 'log_line "FAIL"' in src
    # Validation events are logged with "VALIDATE" tag
    assert 'log_line "VALIDATE"' in src


# ─── Behavioural test: extract validate_npm_cli into a shim and run it ─


def _make_validator_script(tmp_path: Path) -> Path:
    """Extract ONLY `log_line`, `validate_npm_cli` and
    `validate_explain` from install.sh into a standalone bash
    script. Avoids pulling in the surrounding helpers (which
    reference $HEADLESS / $NO_SUDO / wizard logic that aren't
    relevant to the validator's behaviour).
    """
    src = INSTALL_SH.read_text()

    def _slice(start_marker: str, end_marker: str = "\n}\n") -> str:
        idx = src.find(start_marker)
        assert idx > 0, f"marker not found: {start_marker!r}"
        end = src.find(end_marker, idx)
        return src[idx:end + len(end_marker)]

    log_block = _slice("log_line() {")
    val_block = _slice("validate_npm_cli() {")
    exp_block = _slice("validate_explain() {")

    shim = tmp_path / "validator.sh"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"INSTALL_LOG={tmp_path / 'install.log'}\n\n"
        + log_block + "\n"
        + val_block + "\n"
        + exp_block + "\n"
        + '"$@"\n'
    )
    shim.chmod(0o755)
    return shim


def test_validator_returns_1_for_missing_binary(tmp_path):
    """Behavioural: validate_npm_cli "" "X" returns 1
    (binary not found)."""
    validator = _make_validator_script(tmp_path)
    result = subprocess.run(
        ["bash", str(validator), "validate_npm_cli", "", "Test CLI"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1


def test_validator_returns_2_for_non_executable_file(tmp_path):
    """Behavioural: file present but no +x → return 2."""
    validator = _make_validator_script(tmp_path)
    bin_path = tmp_path / "broken-cli"
    bin_path.write_text("#!/bin/sh\necho ok\n")
    bin_path.chmod(0o644)  # NO +x
    result = subprocess.run(
        ["bash", str(validator), "validate_npm_cli",
         str(bin_path), "Broken CLI"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


def test_validator_returns_3_for_stub_with_native_marker(tmp_path):
    """Behavioural: file with +x AND stub-marker content → 3.
    Reproduces the exact 2026-05-08 claude.exe stub."""
    validator = _make_validator_script(tmp_path)
    bin_path = tmp_path / "claude.exe"
    bin_path.write_text(
        '#!/bin/sh\n'
        'echo "Error: claude native binary not installed." >&2\n'
        'echo "" >&2\n'
        'echo "Either postinstall did not run "\n'
    )
    bin_path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(validator), "validate_npm_cli",
         str(bin_path), "Claude Stub"],
        capture_output=True, text=True,
    )
    assert result.returncode == 3, result.stderr


def test_validator_returns_4_when_version_errors(tmp_path):
    """Behavioural: real binary that errors on --version → 4."""
    validator = _make_validator_script(tmp_path)
    bin_path = tmp_path / "errors-cli"
    bin_path.write_text(
        '#!/bin/sh\n'
        'echo "boom" >&2\n'
        'exit 1\n'
    )
    bin_path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(validator), "validate_npm_cli",
         str(bin_path), "Error CLI"],
        capture_output=True, text=True,
    )
    assert result.returncode == 4


def test_validator_returns_0_for_real_working_binary(tmp_path):
    """Behavioural: legit CLI that prints version → 0,
    echoes version on stdout."""
    validator = _make_validator_script(tmp_path)
    bin_path = tmp_path / "good-cli"
    bin_path.write_text(
        '#!/bin/sh\n'
        '[ "$1" = "--version" ] && echo "good-cli v1.2.3" && exit 0\n'
        'exit 0\n'
    )
    bin_path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(validator), "validate_npm_cli",
         str(bin_path), "Good CLI"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "good-cli v1.2.3" in result.stdout


def test_validator_writes_to_install_log(tmp_path):
    """Pin: validate_npm_cli always logs to INSTALL_LOG so
    tests/CI can tail the file for failure diagnosis."""
    validator = _make_validator_script(tmp_path)
    bin_path = tmp_path / "stub"
    bin_path.write_text(
        '#!/bin/sh\necho "Error: claude native binary not installed."\n')
    bin_path.chmod(0o755)
    subprocess.run(
        ["bash", str(validator), "validate_npm_cli",
         str(bin_path), "Stub Test"],
        capture_output=True, text=True,
    )
    log_content = (tmp_path / "install.log").read_text()
    assert "Stub Test" in log_content
    assert "stub detected" in log_content


def test_validate_explain_messages_are_actionable(tmp_path):
    """Pin: each return code maps to a message containing
    actionable next steps (file path / command / fix)."""
    validator = _make_validator_script(tmp_path)
    expected_actionable = {
        1: "not found",
        2: "not executable",
        3: "stub binary",
        4: "--version",
    }
    for code, must_contain in expected_actionable.items():
        result = subprocess.run(
            ["bash", str(validator), "validate_explain", str(code)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert must_contain in result.stdout, (
            f"validate_explain {code} message missing "
            f"{must_contain!r}: {result.stdout!r}"
        )


# ─── Bash syntax + shellcheck-style sanity ─────────────────────────


# ─── Forward-reference / shadowing regression (2026-05-08) ────────


def test_install_sh_log_helpers_defined_before_maybe_launch_gtk_wizard():
    """Bug 2026-05-08: when user ran `./install.sh` on a real GTK VM,
    `maybe_launch_gtk_wizard` invoked `info "GTK desktop session
    detected..."` BEFORE the bash `info()` function was defined.
    Bash fell back to `/usr/bin/info` (texinfo) which printed
    `Brak elementu menu '...' w węźle '(dir)Top'`.

    Pin: log helpers (ok/warn/fail/info/log_line) MUST be defined
    BEFORE the line that calls maybe_launch_gtk_wizard."""
    src = INSTALL_SH.read_text()
    info_def_idx = src.find("\ninfo() {")
    if info_def_idx < 0:
        # try one-line form: `info() { ... }`
        info_def_idx = src.find("\ninfo() ")
    assert info_def_idx > 0, "info() function not found in install.sh"

    wizard_call_idx = src.find("\nmaybe_launch_gtk_wizard\n")
    assert wizard_call_idx > 0, \
        "maybe_launch_gtk_wizard call site not found"

    assert info_def_idx < wizard_call_idx, (
        "info() must be defined BEFORE the maybe_launch_gtk_wizard "
        "call (otherwise wizard's `info '...'` falls through to "
        "/usr/bin/info texinfo binary). "
        f"info_def_idx={info_def_idx}, wizard_call_idx={wizard_call_idx}"
    )


def test_install_sh_log_helpers_unique_no_duplicate_definitions():
    """Pin: each helper is defined exactly once. Refactor that
    moved helpers earlier left a duplicate block which would
    cause subtle issues (last-defined wins in bash but the
    second mkdir/log_line is wasted work)."""
    src = INSTALL_SH.read_text()
    for fn in ("ok()", "warn()", "fail()", "info()", "log_line()"):
        # Match `^<name>` or `\n<name>`
        count = sum(1 for line in src.split("\n") if line.startswith(fn))
        assert count == 1, (
            f"Helper {fn} defined {count} times — should be exactly once"
        )


def test_install_sh_no_bare_info_command_outside_function():
    """Pin: the only `info ...` invocations should be calls to
    the bash function. Detect accidental shell-out via line-by-line
    scan: every `info ...` should follow a function definition."""
    src = INSTALL_SH.read_text()
    info_def_idx = src.find("\ninfo() {")
    if info_def_idx < 0:
        info_def_idx = src.find("\ninfo() ")
    # All `info ` invocations after this point are safe; before it,
    # they would shell out to /usr/bin/info.
    pre_def = src[:info_def_idx]
    for line_num, line in enumerate(pre_def.split("\n"), start=1):
        stripped = line.strip()
        # Skip comments and continuation lines
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith("info ") or stripped.startswith("info\""):
            pytest.fail(
                f"Line {line_num} calls `info` BEFORE function definition "
                f"— would shell out to /usr/bin/info texinfo:\n  {line}"
            )


# ─── Ollama install UX: progress visibility (2026-05-08) ──────────


def test_install_sh_ollama_curl_does_not_silence_progress():
    """Bug 2026-05-08: `curl -fsSL ollama.com/install.sh | sh`
    used `-s` (silent) which hid the 1.5 GB download progress.
    Wizard then appeared to hang on `>>> Installing ollama to
    /usr/local` for many minutes.

    Pin: the curl call wrapping ollama.com install.sh must NOT
    include `-s` (silent) — use `--progress-bar` or noisier
    output so users see download progress."""
    src = INSTALL_SH.read_text()
    # Look at the actual `curl ... | sh` invocation, not warning
    # messages or `add_manual_install` user-runnable command
    # snippets (those legitimately use `-fsSL` since user runs
    # them in a terminal). Heuristic: skip any line that's a
    # quoted string literal (warning text, manual-install command).
    for line in src.splitlines():
        stripped = line.strip()
        # Skip user-message helpers + their continuation lines
        if stripped.startswith(("warn ", "info ", "fail ", "echo ", "#",
                                 "add_manual_install")):
            continue
        # Skip continuation lines that are pure quoted strings
        # (the second arg to add_manual_install on the next line)
        if stripped.startswith('"') and stripped.endswith('"'):
            continue
        if "ollama.com/install.sh" in line and "curl" in line:
            # The `-fsSL` (lowercase s) form silences curl progress
            assert "-fsSL" not in line, (
                f"curl execution line silences progress with -s flag —"
                f" wizard appears hung during 1.5 GB download. Use -fSL"
                f" or --progress-bar instead.\n  {line.strip()}"
            )


def test_install_sh_ollama_install_has_timeout():
    """Pin: ollama install is wrapped in `timeout` so a stuck
    network doesn't hang the wizard indefinitely. Default 30 min
    (1800s) — overridable via OLLAMA_INSTALL_TIMEOUT env var."""
    src = INSTALL_SH.read_text()
    ollama_block_start = src.find('selected_includes "llama"')
    ollama_block_end = src.find("[3/7]", ollama_block_start)
    assert ollama_block_start > 0 and ollama_block_end > 0
    block = src[ollama_block_start:ollama_block_end]
    assert "timeout" in block, (
        "Ollama curl|sh runs without timeout — wizard could hang"
        " forever on a stuck connection."
    )
    assert "OLLAMA_INSTALL_TIMEOUT" in block, (
        "Timeout should be overridable via env var for slow links."
    )


def test_install_sh_ollama_install_uses_line_buffered_io():
    """Pin: ollama install runs through `stdbuf -oL` so the GUI
    wizard sees curl's progress meter (`%/speed/ETA`) live.
    Without line buffering, libc batches stdout and the wizard
    appears frozen between flushes."""
    src = INSTALL_SH.read_text()
    ollama_block_start = src.find('selected_includes "llama"')
    ollama_block_end = src.find("[3/7]", ollama_block_start)
    block = src[ollama_block_start:ollama_block_end]
    assert "stdbuf" in block, (
        "Ollama install should use stdbuf -oL to keep the curl"
        " progress meter visible to GUI wizards in real-time."
    )


def test_install_sh_ollama_install_does_not_use_progress_bar_terse():
    """Pin: --progress-bar shows only `####` without speed/ETA.
    User explicitly asked for speed + ETA — use curl's default
    multi-column meter instead."""
    src = INSTALL_SH.read_text()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("warn ", "info ", "fail ", "echo ", "#")):
            continue
        if "ollama.com/install.sh" in line and "curl" in line:
            assert "--progress-bar" not in line, (
                "curl --progress-bar shows only `####` without speed/ETA."
                " Drop the flag to get default progress meter with"
                " % / Average Speed / Time Left columns."
            )


def test_install_sh_ollama_install_warns_about_download_size():
    """Pin: install.sh emits an info() line warning users about
    the ~1.5 GB download BEFORE starting curl, so they don't
    interpret the inevitable wait as a hang."""
    src = INSTALL_SH.read_text()
    ollama_install_idx = src.find("Installing Ollama via")
    assert ollama_install_idx > 0
    # Look at next 600 chars for size warning
    block = src[ollama_install_idx:ollama_install_idx + 600]
    assert "GB" in block or "MB" in block, (
        "Ollama install line should mention download size so user"
        " doesn't think the installer is hanging."
    )


# ─── Component descriptions during install (2026-05-08) ──────────


def test_install_sh_claude_install_emits_purpose_description():
    """Pin: when installing Claude Code, install.sh emits an
    info() line explaining what it does and why. User shouldn't
    have to guess what each component is for."""
    src = INSTALL_SH.read_text()
    install_block = src[
        src.find('"$CLAUDE_NEEDS_INSTALL" == true'):
        src.find("Stable symlink for GUI launches")
    ]
    assert install_block, "Failed to slice Claude install block"
    assert "Anthropic" in install_block, (
        "Claude install info should mention Anthropic so user knows"
        " whose service this is."
    )
    assert "AI coding agent" in install_block or \
           "coding agent" in install_block


def test_install_sh_copilot_install_emits_purpose_description():
    """Same for Copilot."""
    src = INSTALL_SH.read_text()
    cop_idx = src.find('"$COPILOT_NEEDS_INSTALL" == true')
    install_block = src[
        cop_idx:
        src.find("# Stable symlink for GUI launches", cop_idx)
    ]
    assert install_block, "Failed to slice Copilot install block"
    # Refer to GitHub so user knows which service
    assert "GitHub" in install_block


def test_install_sh_check_tool_emits_purpose_from_deps_json():
    """Pin: check_tool function pulls description from
    defaults/dependencies.json so each apt-installed tool gets
    a one-line "what is this for" message."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("check_tool() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "dep_get" in body and "description" in body, (
        "check_tool should call dep_get to fetch tool descriptions."
    )
    # Both 'required' and 'auto' branches print purpose
    assert body.count("[[ -n \"$purpose\" ]]") >= 2 or \
           body.count('purpose') >= 3


def test_dependencies_json_has_descriptions_for_all_system_tools():
    """Pin: every tool in defaults/dependencies.json system_tools
    has a non-empty `description` field. install.sh now relies on
    this field for user-facing info."""
    import json
    deps_path = REPO_ROOT / "defaults" / "dependencies.json"
    deps = json.loads(deps_path.read_text())
    for tool, spec in deps.get("system_tools", {}).items():
        assert spec.get("description"), (
            f"system_tools.{tool} missing description in dependencies.json"
        )


def test_installer_wizard_ollama_label_shows_realistic_size():
    """Pin (2026-05-08 regression): Ollama label was '~50MB'
    but the actual download is ~1.5 GB (binary + ROCm/CUDA libs).
    Misleading sizes break user trust."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    # Look only at code lines (skip comment lines that may
    # mention the old label as a regression note).
    code_lines = [
        line for line in src.splitlines()
        if not line.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    # The user-visible label MUST NOT contain "50MB"
    assert "~50MB" not in code_only and "~50 MB" not in code_only, (
        "Ollama label still says ~50MB — actual download is ~1.5 GB."
    )
    # New label must mention realistic size
    assert "1.5 GB" in code_only or "1.5GB" in code_only, (
        "Ollama checkbox label should show realistic ~1.5 GB size."
    )


def test_installer_wizard_dep_checkboxes_have_sublabel_with_feature():
    """Pin: each dependency checkbox has a sub-label with
    `dep.feature` (visible description), not just a hover tooltip
    — many users miss tooltips."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    # Look at the populate_picks loop where checkboxes are built
    populate_idx = src.find("def _populate_picks")
    if populate_idx > 0:
        end = src.find("\n    def ", populate_idx + 1)
        block = src[populate_idx:end]
        assert "Sub-label" in block or "sub_lbl" in block, (
            "_populate_picks should add a visible sub-label per dep."
        )
        assert "dep.feature" in block


# ─── Wizard defaults: everything ON unless installed ──────────────


def test_installer_wizard_defaults_all_picks_to_on_always():
    """Pin (2026-05-08): user explicitly asked for opt-out
    semantics — every dep checkbox is default-ON regardless of
    whether the dep is already installed. install.sh's `command -v
    <tool>` detection skips already-present tools, so checking
    them is harmless and lets users force a reinstall easily."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    populate_idx = src.find("def _populate_picks")
    end = src.find("\n    def ", populate_idx + 1)
    block = src[populate_idx:end]
    # The simple rule: every checkbox set_active(True)
    assert "chk.set_active(True)" in block, (
        "_populate_picks should default every checkbox to True"
        " (user explicitly asked for opt-out semantics)."
    )
    # Old conditional rule (only ON for missing) must not regress
    assert "default_on = not (present and present.present)" not in block


def test_installer_wizard_ollama_default_always_on():
    """Pin: Ollama checkbox always default-ON — same rule as
    the rest of the picks. install.sh detects existing ollama
    and short-circuits the curl|sh, so re-selection is idempotent."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    llama_section_start = src.find("_llama_check = Gtk.CheckButton")
    llama_section_end = src.find("self.checks_box.pack_start(self._llama_check",
                                  llama_section_start)
    block = src[llama_section_start:llama_section_end]
    assert "self._llama_check.set_active(True)" in block, (
        "Ollama checkbox should always default ON (user asked"
        " for opt-out semantics)."
    )


# ─── Ollama sudo + heartbeat (2026-05-08) ─────────────────────────


def test_install_sh_ollama_pre_caches_sudo_password():
    """Pin: ollama install.sh internally calls `sudo install`,
    `sudo tar` etc. In `--headless` mode the wizard has no TTY
    to forward a password prompt to — install.sh must cache sudo
    *before* the curl|sh so those internal sudo calls succeed
    without prompting."""
    src = INSTALL_SH.read_text()
    ollama_block_start = src.find('selected_includes "llama"')
    ollama_block_end = src.find("[3/7]", ollama_block_start)
    block = src[ollama_block_start:ollama_block_end]
    assert "sudo -v" in block, (
        "Ollama install must call `sudo -v` before curl|sh to"
        " pre-cache the password — otherwise headless wizard hangs"
        " on the internal `sudo install` prompt."
    )
    # Pre-check: skip cleanly if sudo not available rather than hang
    assert "sudo -n true" in block, (
        "Should test `sudo -n true` first to detect cached vs no-tty"
        " state before triggering an interactive prompt."
    )


def test_install_sh_ollama_emits_heartbeat_during_long_install():
    """Pin: ollama install can take 5-15 min on slow links. A
    backgrounded heartbeat loop emits `...still in progress`
    every 30 s so the GUI wizard / user sees the install isn't
    hung."""
    src = INSTALL_SH.read_text()
    ollama_block_start = src.find('selected_includes "llama"')
    ollama_block_end = src.find("[3/7]", ollama_block_start)
    block = src[ollama_block_start:ollama_block_end]
    assert "HEARTBEAT_PID" in block, (
        "Ollama install should spawn a heartbeat to show liveness."
    )
    assert "still in progress" in block or "still installing" in block, (
        "Heartbeat should print a clear `...still working` message."
    )
    # Heartbeat must be killed on both success and failure paths
    assert block.count("kill \"$HEARTBEAT_PID\"") >= 2, (
        "Heartbeat must be killed on BOTH success and failure paths"
        " — otherwise it keeps emitting after install completes."
    )


# ─── Uninstall + Fix actions (2026-05-08) ────────────────────────


def test_install_sh_supports_uninstall_flag():
    """Pin: --uninstall flag triggers do_uninstall function."""
    src = INSTALL_SH.read_text()
    assert "--uninstall)" in src
    assert "do_uninstall()" in src
    assert "ACTION=\"uninstall\"" in src


def test_install_sh_supports_fix_flag():
    """Pin: --fix flag triggers do_fix repair."""
    src = INSTALL_SH.read_text()
    assert "--fix)" in src
    assert "do_fix()" in src


def test_install_sh_uninstall_removes_all_known_artifacts():
    """Pin: do_uninstall removes BT files, symlinks, desktop entry,
    icon, AND npm-installed CLI (claude/copilot)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_uninstall() {")
    fn_end = src.find("\ndo_fix()", fn_idx)
    body = src[fn_idx:fn_end]
    assert "rm -rf \"$INSTALL_DIR\"" in body
    assert "_BT_BIN_SYMLINKS" in body
    assert ".desktop" in body
    assert "@anthropic-ai" in body  # claude removal
    assert "@github" in body  # copilot removal


def test_install_sh_uninstall_supports_purge_for_user_data():
    """Pin: --purge with --uninstall also removes ~/.config/bterminal
    + ~/.claude-context. Default uninstall preserves user data."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_uninstall() {")
    fn_end = src.find("\ndo_fix()", fn_idx)
    body = src[fn_idx:fn_end]
    assert '"$PURGE" == true' in body
    assert "$CTX_DIR" in body
    assert "$CONFIG_DIR" in body
    # Default (no purge) must explicitly state data is preserved
    assert "preserved" in body or "User data" in body


def test_install_sh_fix_repairs_broken_symlinks_in_place():
    """Pin: do_fix re-creates symlinks for missing CLI tools
    when source files exist."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n# Dispatch action", fn_idx)
    body = src[fn_idx:fn_end]
    assert "ln -sf" in body
    assert "ctx tasks consult memory_wizard claude_log" in body or \
        "tool in ctx" in body


def test_install_sh_action_dispatch_runs_before_wizard_autospawn():
    """Pin: --uninstall and --fix execute BEFORE
    maybe_launch_gtk_wizard. Otherwise an `--uninstall` invocation
    would launch the GUI wizard (confusing) instead of doing the
    headless cleanup."""
    src = INSTALL_SH.read_text()
    dispatch_idx = src.find("# Dispatch action — uninstall exits")
    wizard_idx = src.find("\nmaybe_launch_gtk_wizard\n")
    assert dispatch_idx > 0
    assert wizard_idx > 0
    assert dispatch_idx < wizard_idx, (
        "Action dispatch must precede wizard auto-spawn."
    )


def test_install_sh_help_documents_uninstall_and_fix():
    """Pin: --help describes both new actions + --purge."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--uninstall" in result.stdout
    assert "--fix" in result.stdout
    assert "--purge" in result.stdout


def test_install_sh_selected_none_skips_all_auto_deps():
    """Pin: --selected none (literal) skips ALL auto-tier deps.
    Was needed because empty `--selected ''` legacy-meant "try all"."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("selected_includes()")
    fn_end = src.find("\n}", fn_idx)
    body = src[fn_idx:fn_end]
    assert '"$SELECTED_DEPS" == "none"' in body


def test_install_sh_ollama_skip_uses_flag_not_return():
    """Pin (regression for 2026-05-08 hang): ollama install must
    use a flag (`OLLAMA_DO_INSTALL=false`) to skip, not `return 0`
    which doesn't propagate from top-level scope. Without the
    flag, curl|sh ran anyway and triggered the second sudo prompt."""
    src = INSTALL_SH.read_text()
    ollama_block_start = src.find('selected_includes "llama"')
    ollama_block_end = src.find("[3/7]", ollama_block_start)
    block = src[ollama_block_start:ollama_block_end]
    assert "OLLAMA_DO_INSTALL=" in block
    assert '"$OLLAMA_DO_INSTALL" == true' in block


# ─── Wizard upfront sudo prompt (2026-05-08) ─────────────────────


def test_wizard_has_picks_need_sudo_helper():
    """Pin: wizard detects when selected deps need root."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_picks_need_sudo" in src
    # Must include apt-tier deps + ollama as sudo-needing
    assert "meld" in src
    assert "llama" in src or "ollama" in src


def test_wizard_prompts_for_sudo_password_via_gtk_dialog():
    """Pin: wizard shows a modal Gtk.Dialog with password Entry
    BEFORE spawning install.sh. Without this, install.sh's
    interactive sudo prompts hit a non-existent TTY and hang."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_prompt_sudo_password" in src
    fn_idx = src.find("def _prompt_sudo_password")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Password masking
    assert "set_visibility(False)" in body
    # Returns None on cancel
    assert "return password" in body or "password if response" in body


def test_wizard_caches_sudo_via_dash_S_dash_v():
    """Pin: wizard sends password to `sudo -S ... -v` (read from
    stdin, validate only, no command). Cache lasts ~15 min."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    # `-S` (stdin password) + `-v` (validate, no command) MUST be
    # present together in the argv. `-p ""` (empty prompt) was added
    # 2026-05-08 to suppress the prompt on pipes.
    fn_idx = src.find("def _cache_sudo")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert '"-S"' in body and '"-v"' in body


def test_wizard_keeps_sudo_cache_alive_during_install():
    """Pin: wizard refreshes sudo cache every 4 min via background
    `sudo -n -v` loop (cache default 15 min — keepalive prevents
    expiry on long ollama download)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_start_sudo_keepalive" in src
    assert "_stop_sudo_keepalive" in src
    assert "timeout_add_seconds(240" in src or \
           "timeout_add_seconds(\n            240" in src
    assert '"sudo", "-n", "-v"' in src


def test_wizard_stops_sudo_keepalive_when_install_done():
    """Pin: keepalive must be stopped on _on_install_done (success
    OR cancel) — otherwise the GLib timeout keeps firing forever."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _on_install_done")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_stop_sudo_keepalive" in body


def test_wizard_falls_back_to_no_sudo_when_user_cancels_password():
    """Pin: if user cancels the sudo prompt, wizard runs install.sh
    with --no-sudo so BT files at least get installed (apt deps +
    ollama skipped instead of hanging)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _start_install")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "no_sudo=True" in body
    # Cancel branch logs informational message
    assert "Sudo cancelled" in body or "cancelled" in body.lower()


def test_build_install_argv_supports_action_parameter():
    """Pin: build_install_argv accepts action='install'/'fix'/
    'uninstall' and passes the right flag to install.sh."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def build_install_argv(")
    fn_end = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "action" in body
    assert '"--fix"' in body
    assert '"--uninstall"' in body
    assert "purge" in body


def test_detect_install_state_helper_exists():
    """Pin: detect_install_state() pure helper for wizard to gate
    Install/Fix/Uninstall buttons in welcome page."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "def detect_install_state" in src
    fn_idx = src.find("def detect_install_state")
    fn_end = src.find("\n\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "not_installed" in body
    assert "installed" in body
    assert "broken" in body
    assert "bterminal-launcher" in body or "bterminal" in body
    assert "__init__.py" in body


# ─── Wizard radio + state machine (Install/Fix/Uninstall) ────────


def test_wizard_welcome_page_has_3_action_radio_buttons():
    """Pin: welcome page exposes 3 RadioButtons (Install/Fix/Uninstall)
    so user picks the action upfront. Replaces old single-flow."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _build_page_welcome")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "RadioButton.new_with_label_from_widget" in body
    assert "Install BTerminal" in body
    assert "Fix existing install" in body
    assert "Uninstall BTerminal" in body
    assert "_action_radios" in body


def test_wizard_radio_sensitivity_matches_install_state():
    """Pin: radio sensitivity tracks install state:
      - not_installed → only Install enabled
      - installed     → Install disabled, Fix/Uninstall enabled
      - broken        → all 3 enabled (Install replaces, Fix repairs)
    """
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _build_page_welcome")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Install disabled when already installed
    assert 'self._install_state != "installed"' in body
    # Fix/Uninstall enabled only when something to fix/remove
    assert '("installed", "broken")' in body


def test_wizard_default_action_matches_install_state():
    """Pin: defaults to Fix when BT already present, Install otherwise."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _build_page_welcome")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "rb_fix.set_active(True)" in body
    assert 'self._action = "fix"' in body
    assert "rb_install.set_active(True)" in body


def test_wizard_has_uninstall_confirm_page():
    """Pin: uninstall flow has its own confirmation page listing
    what will be removed + opt-in `--purge` checkbox."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_build_page_uninstall_confirm" in src
    assert "uninstall_confirm" in src
    assert "Also delete my user data" in src
    assert "chk_purge" in src


def test_wizard_pages_by_action_skips_irrelevant_pages():
    """Pin: Fix flow skips inventory + picks (only welcome→progress→
    summary). Uninstall skips inventory + picks but adds confirm
    (welcome→confirm→progress→summary). Install uses the full 5
    pages."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    # Action sequences are tuples of indices into PAGES
    assert "PAGES_BY_ACTION" in src
    fn_idx = src.find("PAGES_BY_ACTION = {")
    fn_end = src.find("}", fn_idx) + 1
    block = src[fn_idx:fn_end]
    # install: 5 pages (no confirm)
    assert '"install":' in block and "(0, 1, 2, 4, 5)" in block
    # fix: skip 1, 2, 3 (inventory, picks, confirm)
    assert '"fix":' in block and "(0, 4, 5)" in block
    # uninstall: skip 1, 2 — keep 3 (confirm)
    assert '"uninstall":' in block and "(0, 3, 4, 5)" in block


def test_wizard_navigation_uses_action_aware_helpers():
    """Pin: run_and_install + Back/Next use _next_page_idx /
    _prev_page_idx instead of `current+1` so page-skip per action
    works."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def run_and_install")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_next_page_idx(self._current_page)" in body
    assert "_prev_page_idx(self._current_page)" in body
    # Old hardcoded +1 / -1 must be gone
    assert "self._current_page + 1" not in body
    assert "self._current_page - 1" not in body


def test_wizard_next_button_label_shows_action_verb():
    """Pin: on the last input page before progress the Next button
    shows action-specific verb ('Install →', 'Repair →',
    'Uninstall →') instead of generic 'Next →'."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _update_nav_buttons")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert '"Install →"' in body
    assert '"Repair →"' in body
    assert '"Uninstall →"' in body


def test_wizard_start_install_passes_action_and_purge_to_argv():
    """Pin: _start_install reads self._action + self._purge into
    build_install_argv so the right install.sh flag is used."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _start_install")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "action=self._action" in body
    assert "purge=self._purge" in body


# ─── Wizard transitions to summary correctly post-refactor ────────


def test_wizard_on_install_done_jumps_to_summary_index_5():
    """Pin (regression for 2026-05-08): after the uninstall_confirm
    page was inserted at index 3, the summary index moved from 4
    to 5. _on_install_done must call _show_page(5), not 4 — else
    the wizard hangs on the progress page after uninstall finishes."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _on_install_done")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "self._show_page(5)" in body, (
        "Summary page index moved from 4 to 5 after uninstall_confirm"
        " was inserted; _on_install_done must use 5."
    )
    assert "self._show_page(4)" not in body


def test_install_sh_uninstall_emits_progress_status_json():
    """Pin: do_uninstall emits status_json updates throughout —
    wizard's progress bar would stick at 0% otherwise (the original
    2026-05-08 bug screenshot)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_uninstall() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert body.count("status_json uninstall") >= 4, (
        "do_uninstall must emit multiple status_json lines so the"
        " wizard's progress bar moves from 0% to 100%."
    )
    # Final 100% marker
    assert "status_json done ok 100" in body


def test_install_sh_fix_emits_progress_status_json():
    """Same for do_fix."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert body.count("status_json fix") >= 3
    # Either fix completes with done-100 OR falls through to install
    # which has its own status_json done line.
    assert ("status_json done ok 100" in body or
            "ACTION=\"install\"" in body)


def test_wizard_summary_message_matches_action():
    """Pin: summary header says 'Installation finished.' / 'Repair
    finished.' / 'Uninstall finished.' depending on action."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _populate_summary")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "Installation finished." in body
    assert "Repair finished." in body
    assert "Uninstall finished." in body


# ─── Diagnostic capture + bug-report bundle (2026-05-08) ─────────


def test_install_sh_emits_diag_block_on_every_run():
    """Pin: install.sh writes [DIAG] lines to install.log at the
    start of every run — OS / kernel / arch / locale / RAM. Lets
    bug reports include reproducible system context."""
    src = INSTALL_SH.read_text()
    assert 'log_line "DIAG"' in src
    assert "/etc/os-release" in src
    assert "uname -srm" in src
    assert "uname -m" in src
    assert "free -h" in src
    assert "df -h" in src


def test_install_sh_writes_per_run_log_to_install_runs_dir():
    """Pin: each run gets a timestamped log file in
    ~/.config/bterminal/install-runs/ for bug-report attachments."""
    src = INSTALL_SH.read_text()
    assert "RUN_LOG_DIR=" in src
    assert "install-runs" in src
    assert "RUN_LOG_FILE=" in src


def test_wizard_writes_per_run_log_during_install():
    """Pin: wizard tees subprocess output to a per-run log file
    so even raw output (with ANSI codes) is captured for diagnosis."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _start_install")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_run_log_fp" in body
    assert "install-runs" in body or "wizard-run-" in body


def test_wizard_handle_install_line_tees_to_file():
    """Pin: every line of subprocess output is written to the
    per-run log AND the in-memory log view."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _handle_install_line")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_run_log_fp" in body
    assert "_run_log_fp.write" in body


def test_wizard_summary_page_has_save_diagnostic_button():
    """Pin: summary page has 'Save diagnostic report' button so
    users can bundle install.log + per-run logs + system info into
    a single tar.gz for bug reports."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_save_diagnostic_bundle" in src
    assert "Save diagnostic report" in src
    fn_idx = src.find("def _save_diagnostic_bundle")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Bundle includes: install.log + install_errors.json + run logs
    assert "install.log" in body
    assert "install_errors.json" in body
    assert "install-runs" in body
    assert "tarfile.open" in body or "tar.add" in body


def test_wizard_collect_runtime_diagnostics_captures_os_info():
    """Pin: _collect_runtime_diagnostics produces a snapshot
    suitable for bug reports — Python version, platform, GTK,
    locale, AI CLI versions."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _collect_runtime_diagnostics")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Must capture each of these
    for must_have in (
        "platform", "python_version", "uname",
        "/etc/os-release", "claude", "copilot", "ollama",
        "GTK", "DISPLAY",
    ):
        assert must_have in body, f"diagnostic missing: {must_have}"


def test_wizard_summary_page_has_open_logs_folder_button():
    """Pin: 'Open logs folder' button uses xdg-open to surface
    ~/.config/bterminal/ for users who want to inspect raw files."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_open_logs_folder" in src
    assert "Open logs folder" in src
    fn_idx = src.find("def _open_logs_folder")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "xdg-open" in body


# ─── No-sudo no longer blocks AI CLI installs (2026-05-08) ────────


def test_install_sh_claude_install_works_in_no_sudo_mode():
    """Pin (regression): old install.sh skipped Claude Code
    install when --no-sudo was set. But npm install -g uses
    ~/.npm-global (user-owned) so it doesn't need root.
    --no-sudo should ONLY skip apt installs, not AI CLIs."""
    src = INSTALL_SH.read_text()
    # The skip-message should NOT appear anywhere any more
    assert 'Claude Code not found (skipped — no-sudo mode)' not in src
    assert 'Copilot CLI not found (skipped — no-sudo mode)' not in src


def test_wizard_retries_sudo_prompt_up_to_3_times():
    """Pin: wizard gives 3 tries for sudo password before falling
    back to --no-sudo. Old behaviour: single attempt → fallback."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _start_install")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "range(1, 4)" in body or "for attempt in" in body
    assert "error_hint" in body  # retry shows red error in dialog
    assert "Wrong password" in body or "try again" in body


def test_wizard_cache_sudo_clears_stale_creds_first():
    """Pin: _cache_sudo runs `sudo -k` before -S -v so wrong
    passwords definitely fail (otherwise sudo's grace period
    can return success for any string)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _cache_sudo")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert '"sudo", "-k"' in body or "'sudo', '-k'" in body


def test_install_sh_uninstall_removes_xdg_desktop_shortcut():
    """Pin (regression for 2026-05-08): user reported `~/Pulpit/
    bterminal.desktop` left after uninstall (Polish locale uses
    'Pulpit' for the Desktop folder). do_uninstall must clean
    the XDG_DESKTOP_DIR + locale-localized fallbacks."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_uninstall() {")
    fn_end = src.find("\ndo_fix()", fn_idx)
    body = src[fn_idx:fn_end]
    assert "xdg-user-dir DESKTOP" in body, (
        "Should query xdg-user-dir for the user's actual Desktop folder."
    )
    # Locale fallbacks for common languages
    for path in ("$HOME/Desktop", "$HOME/Pulpit",
                 "$HOME/Bureau", "$HOME/Schreibtisch"):
        assert path in body, f"Missing locale fallback: {path}"
    # Must rm bterminal.desktop from each
    assert "bterminal.desktop" in body
    assert "rm -f " in body


def test_wizard_finish_button_says_close_after_uninstall():
    """Pin: summary page after uninstall shows 'Close' (not
    'Open BTerminal' which makes no sense post-uninstall)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _update_nav_buttons")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert 'self._action == "uninstall"' in body
    assert '"Close"' in body
    assert 'set_label("Open BTerminal")' in body or \
           'set_label("Close")' in body


# ─── Manual install fallback + verification (2026-05-08) ────────


def test_install_sh_has_manual_installs_block_at_end():
    """Pin: install.sh emits a 'Manual install needed' summary
    block listing components that couldn't be installed +
    user-runnable commands. Re-running install.sh detects these
    and skips."""
    src = INSTALL_SH.read_text()
    assert "MANUAL_INSTALLS=()" in src
    assert "add_manual_install" in src
    assert "Manual install" in src or "manual installation" in src
    assert "re-run ./install.sh" in src or "re-run install.sh" in src


def test_install_sh_ai_cli_failures_do_not_block_install():
    """Pin: when Claude/Copilot npm install fails, install.sh emits
    `warn` (not `fail`) + adds to MANUAL_INSTALLS — so user can
    still get BT core working without hard exit. Required deps
    (git, ssh) still trigger fail."""
    src = INSTALL_SH.read_text()
    # Find the Claude install branch
    claude_idx = src.find('"$CLAUDE_NEEDS_INSTALL" == true')
    claude_end = src.find("[2.5/7] GitHub Copilot", claude_idx)
    block = src[claude_idx:claude_end]
    assert "fail \"Claude Code installation failed" not in block
    assert "warn \"Claude Code installation failed" in block
    assert "add_manual_install \"Claude Code\"" in block


def test_install_sh_runs_post_install_verification():
    """Pin: end of install.sh runs VERIFY_ERRORS check — BT package
    + launcher + companion CLIs MUST exist or script exits non-zero
    even when ERRORS array is empty. Catches silent file-copy
    failures."""
    src = INSTALL_SH.read_text()
    assert "VERIFY_ERRORS=()" in src
    assert "$INSTALL_DIR/bterminal/__init__.py" in src
    assert "$BIN_DIR/bterminal" in src
    # Verify must trigger exit 1 + status_json failed
    verify_idx = src.find("VERIFY_ERRORS=()")
    verify_end = src.find("status_json done ok 100", verify_idx)
    block = src[verify_idx:verify_end]
    assert "exit 1" in block
    assert "Install verification failed" in block
    assert 'status_json done failed 100' in block


def test_install_sh_verify_includes_post_install_cli_validation():
    """Pin: post-install verify re-validates AI CLIs that were
    claimed to be installed. Catches the case where install branch
    set EXISTING_CLAUDE but the binary is actually a stub /
    broken."""
    src = INSTALL_SH.read_text()
    verify_idx = src.find("VERIFY_ERRORS=()")
    verify_end = src.find("status_json done ok 100", verify_idx)
    block = src[verify_idx:verify_end]
    assert "validate_npm_cli" in block
    assert "EXISTING_CLAUDE" in block
    assert "EXISTING_COPILOT" in block


def test_install_sh_emits_status_json_failed_on_error_path():
    """Pin: install.sh emits `status_json done failed 100` on the
    error exit so the wizard can flag the run as a failure on the
    summary page (no false 'Installation finished' banner)."""
    src = INSTALL_SH.read_text()
    assert 'status_json done failed 100' in src
    # Both error paths must emit it
    error_paths = src.count('status_json done failed 100')
    assert error_paths >= 2, (
        f"Expected at least 2 'status_json done failed' lines"
        f" (ERRORS path + VERIFY_ERRORS path), found {error_paths}"
    )


def test_wizard_summary_shows_failure_when_install_rc_nonzero():
    """Pin: if install.sh exited non-zero, summary shows red
    FAILED banner instead of 'Installation finished'. Pre-fix:
    wizard ignored returncode and always claimed success."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _populate_summary")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_install_rc" in body
    assert 'rc != 0' in body
    assert "FAILED" in body or "failed" in body.lower()
    # Must still flip btn label to Close (not Open BTerminal)
    assert 'set_label("Close")' in body


def test_wizard_captures_subprocess_exit_code():
    """Pin: _on_install_done stores proc.get_exit_status() in
    self._install_rc so summary can decide success/failure."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _on_install_done")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "get_exit_status" in body
    assert "_install_rc" in body


def test_wizard_aborts_install_on_3x_failed_sudo():
    """Pin (regression for 2026-05-08): after 3 failed sudo
    attempts, wizard prompts 'Continue without sudo' / 'Abort'.
    Default is Abort. Old behaviour silently ran with --no-sudo."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_prompt_sudo_failed_choice" in src
    fn_idx = src.find("def _prompt_sudo_failed_choice")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert '"abort"' in body
    assert "skip_sudo" in body
    # Default must be Abort (response 2)
    assert "set_default_response(2)" in body
    # Both buttons present
    assert "Continue without sudo" in body
    assert "Abort install" in body


# ─── Purge survives log-write after CONFIG_DIR removed ────────────


def test_install_sh_purge_redirects_log_after_config_removal():
    """Pin (regression for 2026-05-08): do_uninstall --purge
    deletes $CONFIG_DIR mid-flow, but log_line() then tries to
    append to $INSTALL_LOG (= $CONFIG_DIR/install.log) which fails
    under `set -e` → uninstall exits non-zero.

    Fix: copy current install.log to /tmp BEFORE rm -rf $CONFIG_DIR,
    then point INSTALL_LOG at the /tmp copy so subsequent log_line
    calls keep working."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_uninstall() {")
    fn_end = src.find("\ndo_fix()", fn_idx)
    body = src[fn_idx:fn_end]
    # Must mktemp before rm -rf
    mktemp_idx = body.find("mktemp -t bterminal-uninstall-final")
    rm_idx = body.find('rm -rf "$CONFIG_DIR" "$CTX_DIR"')
    assert mktemp_idx > 0 and rm_idx > 0
    assert mktemp_idx < rm_idx, (
        "Must save install.log to /tmp BEFORE removing CONFIG_DIR"
    )
    # INSTALL_LOG must be redirected after purge
    redirect_idx = body.find("INSTALL_LOG=\"$FINAL_LOG_TMP\"")
    assert redirect_idx > rm_idx, (
        "INSTALL_LOG must be redirected to /tmp AFTER the purge so"
        " subsequent log_line calls don't crash."
    )


def test_wizard_open_logs_folder_handles_missing_dir():
    """Pin: _open_logs_folder gracefully handles purged config
    dir (no silent failure — informs user)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _open_logs_folder")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "cfg_dir.is_dir()" in body
    assert "Logs folder removed" in body or \
           "purge" in body.lower()
    # Must look for /tmp fallback log
    assert "bterminal-uninstall-final" in body


def test_wizard_open_logs_folder_uses_multiple_strategies():
    """Pin: _open_logs_folder tries xdg-open, gio open, AND
    Gio.AppInfo.launch_default_for_uri before giving up.
    Single-strategy code can fail silently on minimal desktops."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _open_logs_folder")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "xdg-open" in body
    assert '"gio", "open"' in body or "'gio', 'open'" in body
    assert "Gio.AppInfo.launch_default_for_uri" in body
    # If everything fails, fall back to error dialog (no silent fail)
    assert "_show_error_dialog" in body


def test_wizard_save_diagnostic_uses_tmp_log_after_purge():
    """Pin: _save_diagnostic_bundle finds /tmp fallback log and
    includes it in the tar.gz when CONFIG_DIR was purged."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _save_diagnostic_bundle")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "purge_log" in body
    assert "bterminal-uninstall-final" in body
    assert "purge-fallback" in body  # arcname inside tarball


# ─── Sudo password diagnosis (2026-05-08) ────────────────────────


def test_wizard_cache_sudo_returns_diagnostic_message():
    """Pin: _cache_sudo returns (success, diagnostic) — diagnostic
    is a short string explaining WHY auth failed (wrong password
    vs no TTY vs not in sudoers vs timeout). Old version returned
    just bool — user couldn't tell why their correct password
    was rejected."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _cache_sudo")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Returns tuple
    assert "-> tuple" in body
    assert "return (True," in body
    assert "return (False," in body
    # Recognizes specific failure modes
    for token in ("incorrect password", "no tty", "not in the sudoers",
                  "no password was provided", "timed out"):
        assert token in body.lower(), (
            f"Missing sudo failure diagnosis: {token!r}"
        )


def test_wizard_cache_sudo_uses_bytes_with_explicit_utf8():
    """Pin: password sent to sudo as bytes with explicit utf-8
    encoding — handles non-ASCII passwords (Polish ą/ę, German ü)
    that text=True can corrupt under exotic locales."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _cache_sudo")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert '.encode("utf-8")' in body
    # `text=True` keyword arg must NOT be passed to subprocess.run
    # (it's incompatible with bytes input). Check the actual call,
    # ignoring the comment that explains why we avoid it.
    code_lines = [ln for ln in body.splitlines()
                  if not ln.strip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert "text=True" not in code_only, (
        "text=True kwarg passed to sudo subprocess — must use bytes mode."
    )
    # capture_output is needed for proper decode
    assert "capture_output=True" in body


def test_wizard_cache_sudo_forces_C_locale_for_error_parsing():
    """Pin: sudo's stderr is parsed for failure diagnosis. Set
    LANG=C / LC_ALL=C in env so messages are in English regardless
    of system locale (was being shown 'Niestety...' in Polish on
    pl_PL locale, which the parser couldn't recognise)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _cache_sudo")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert 'env_c["LANG"] = "C"' in body
    assert 'env_c["LC_ALL"] = "C"' in body or \
           'env_c["LC_MESSAGES"] = "C"' in body


def test_wizard_logs_sudo_stderr_for_diagnosis():
    """Pin: every sudo attempt's stderr + rc gets appended to the
    install log so users can attach diagnostic bundles when sudo
    keeps rejecting their (correct) password. Logged to FILE
    (~/.config/bterminal/install.log) — NOT to the GUI log view
    which the user sees during install (that would flood with
    internal noise)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _cache_sudo")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "install.log" in body  # file destination
    assert "[SUDO]" in body or "[sudo]" in body
    assert "stderr=" in body


def test_wizard_retry_dialog_shows_specific_diagnosis():
    """Pin: sudo retry dialog shows the actual reason from
    `_cache_sudo`'s diagnostic field, not the generic 'Wrong
    password — try again'. Otherwise users with TTY / sudoers
    issues keep retyping the right password thinking they typo'd."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _start_install")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Must call _cache_sudo as tuple-unpack
    assert "ok, hint = self._cache_sudo(password)" in body
    # last_hint is fed into next prompt's error_hint
    assert "last_hint" in body


# ─── Download policy + progress visibility (2026-05-08) ─────────


def test_install_sh_documents_download_policy_at_top():
    """Pin: install.sh has a DOWNLOAD POLICY comment block near
    the top that mandates progress visibility for every download.
    Future contributors must respect it."""
    src = INSTALL_SH.read_text()
    head = src[:3000]  # check first ~80 lines
    assert "DOWNLOAD POLICY" in head
    assert "% completed" in head or "progress" in head.lower()
    # Specific bans
    assert "BANNED" in head or "are banned" in head or "MUST show" in head
    # References to the 4 strategies (curl, wget, sub-script hook, heartbeat)
    assert "curl" in head and "wget" in head
    assert "heartbeat" in head.lower()


def test_install_sh_hooks_curl_for_ollama_subscript():
    """Pin: ollama install.sh internally calls curl with
    --progress-bar (terse `####` only). install.sh hooks curl via
    a PATH-injected wrapper that strips --progress-bar so curl's
    default multi-column meter (% / speed / ETA) takes over for
    the inner ollama-linux-amd64.tar.zst download."""
    src = INSTALL_SH.read_text()
    ollama_block_start = src.find('selected_includes "llama"')
    ollama_block_end = src.find("[3/7]", ollama_block_start)
    block = src[ollama_block_start:ollama_block_end]
    # PATH hook in place
    assert "CURL_HOOK_DIR" in block
    assert 'PATH="$CURL_HOOK_DIR:$PATH"' in block
    # The hook script strips --progress-bar / -# / --silent / -s
    assert "--progress-bar" in block
    assert "REAL_CURL=" in block
    # Cleanup after install (success AND failure paths)
    assert block.count('rm -rf "$CURL_HOOK_DIR"') >= 2


def test_wizard_handle_install_line_filters_progress_pct_floods():
    """Pin (2026-05-08 UX bug): ollama tar.zst download outputs
    `0.1% / 0.2% / 0.3% ...` 1000+ lines via \\r-update progress
    meter. GTK TextView treats each as a separate line and floods
    the log view. Filter: short pct-only lines update the progress
    bar text but DON'T get appended to the log."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    # Class has a compiled regex for progress lines
    assert "_RX_PROGRESS_PCT" in src
    # Handler returns early on a match (no _append_log)
    fn_idx = src.find("def _handle_install_line")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_RX_PROGRESS_PCT.match" in body
    assert "Downloading… " in body or "Downloading…" in body


def test_wizard_handle_install_line_silences_curl_header():
    """Pin: curl's multi-column header `% Total / Dload Upload` /
    multi-column data rows don't flood the log either."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_RX_CURL_HEADER" in src
    assert "_RX_CURL_DATA_ROW" in src


def test_wizard_cache_sudo_logs_to_file_not_gui():
    """Pin: sudo diagnostic [SUDO] lines go to ~/.config/bterminal/
    install.log file — NOT to the GUI _append_log (would clutter
    user-facing display with internal subprocess noise). The
    diagnostic_bundle still picks up the file."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _cache_sudo")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # MUST write to the install.log file
    assert "install.log" in body and 'open(' in body
    # MUST NOT push [SUDO] to the GUI log view (was the bug)
    assert "_append_log" not in body, (
        "Sudo diagnostics must NOT be appended to the GUI log view —"
        " write to install.log file directly."
    )


def test_install_sh_diag_uses_C_locale_for_free_and_df():
    """Pin (regression for 2026-05-08): on pl_PL.UTF-8 / de_DE
    locales `free -h` outputs 'pamięć:' / 'Speicher:' instead of
    'Mem:', so our `awk '/^Mem:/'` failed → DIAG line said
    'free unavailable'. Force LANG=C in the diag block."""
    src = INSTALL_SH.read_text()
    diag_idx = src.find("# Diagnostic block")
    if diag_idx < 0:
        diag_idx = src.find('log_line "DIAG"')
    assert diag_idx > 0
    # Find the {} block
    brace_open = src.find("{", diag_idx)
    brace_close = src.find("} 2>/dev/null", brace_open)
    block = src[brace_open:brace_close]
    assert "LANG=C free -h" in block
    assert "LANG=C df -h" in block


def test_wizard_sets_default_response_on_every_input_page():
    """Pin (regression for 2026-05-08): without set_default_response,
    Return on a focused checkbox toggled the checkbox instead of
    advancing to the next page (E2E test lost a randomly-focused
    pdflatex checkbox state). Default response makes Return
    activate Next regardless of which child has focus."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _show_page")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "set_default_response" in body
    assert "_WIZARD_NEXT" in body
    # Summary uses OK (Open BTerminal / Close button)
    assert "Gtk.ResponseType.OK" in body


def test_install_sh_apt_install_uses_sudo_askpass_when_available():
    """Pin (regression for 2026-05-08): sudo 1.9+ uses
    `tty_tickets` by default — cache is per-TTY, so a wizard
    subprocess spawned via Gio.Subprocess can't see the wizard's
    `sudo -v` cache. Fix: wizard writes a SUDO_ASKPASS script
    with the password, install.sh's apt_install uses `sudo -A`
    which reads SUDO_ASKPASS regardless of TTY."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("apt_install() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "SUDO_ASKPASS" in body
    assert "sudo -A apt-get install" in body
    # Fallback for interactive flow without SUDO_ASKPASS
    assert "[[ -t 0 && -t 1 ]]" in body


def test_wizard_writes_sudo_askpass_script_for_apt_handoff():
    """Pin: wizard's _setup_sudo_askpass writes a temp script
    holding the password (mode 0700) and stores the path in
    self._sudo_askpass_path. SUDO_ASKPASS env var is set on the
    install.sh subprocess via Gio.SubprocessLauncher.setenv()."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    assert "_setup_sudo_askpass" in src
    fn_idx = src.find("def _setup_sudo_askpass")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "tempfile.mkstemp" in body
    assert "0o700" in body  # mode

    # Spawn point uses launcher.setenv("SUDO_ASKPASS", ...)
    spawn_idx = src.find("launcher.spawnv(argv)")
    spawn_block = src[spawn_idx - 500:spawn_idx]
    assert "setenv" in spawn_block
    assert '"SUDO_ASKPASS"' in spawn_block


def test_wizard_cleans_up_sudo_askpass_after_install():
    """Pin: _on_install_done MUST delete the SUDO_ASKPASS file —
    leaving it behind exposes the password on disk."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def _on_install_done")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_cleanup_sudo_askpass" in body
    # Cleanup function actually unlinks
    cu_idx = src.find("def _cleanup_sudo_askpass")
    cu_end = src.find("\n    def ", cu_idx + 1)
    cu_body = src[cu_idx:cu_end]
    assert "os.unlink" in cu_body


def test_install_sh_apt_install_emits_heartbeat_during_download():
    """Pin (UX, 2026-05-08): apt-get install on big packages
    (texlive-latex-extra ≈ 150 MB) takes minutes. Without a
    heartbeat the wizard log view stays static — users assume
    the installer froze. apt_install spawns a background loop
    emitting `...still installing $1...` every 10 s for the
    duration of the apt-get child process."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("apt_install() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "still installing" in body
    assert "kill -0" in body and "sleep 10" in body
    # apt-get is run async (& + wait) so we can supervise the PID
    assert "apt_pid=" in body or "apt_pid=$!" in body


def test_install_sh_apt_install_does_not_use_qq_silent_flag():
    """Pin: `-qq` (quiet) on apt-get install hides Get: + %
    progress lines, defeating the wizard's progress filter
    (DOWNLOAD POLICY rule 1 — every download must show progress)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("apt_install() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    # `-qq` MUST not appear in the actual apt-get install lines
    for line in body.splitlines():
        if "apt-get install" in line:
            assert "-qq" not in line, (
                f"apt-get install line uses -qq (silent) which hides"
                f" download progress:\n  {line.strip()}"
            )


# ─── Uninstall E2E test runner — task #140 ─────────────────────


def test_uninstall_e2e_runner_covers_5_required_subtests():
    """Pin (#140): tools/test_wizard_e2e_vm.sh run_mode_uninstall
    covers the 5 sub-tests from task description:
      (a) uninstall without --purge — BT removed, configs preserved
      (b) uninstall --purge — everything removed (incl. user data)
      (c) XDG_DESKTOP_DIR shortcut (Pulpit/Desktop/...) removed
      (d) AI CLIs (claude/copilot npm) removed
      (e) wizard-mode summary shows uninstall completed marker
    """
    runner = REPO_ROOT / "tools" / "test_wizard_e2e_vm.sh"
    src = runner.read_text()
    fn_idx = src.find("run_mode_uninstall() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    # Each sub-test labelled with letter prefix
    for tag in ("(a)", "(b)", "(c)", "(d)", "(e)"):
        assert tag in body, f"Sub-test {tag} missing from runner"
    # And each has its own pass/fail step
    assert body.count("step_pass") >= 5
    assert body.count("step_fail") >= 5


def test_uninstall_e2e_runner_plants_pulpit_shortcut_pretest():
    """Pin (c): runner plants ~/.local/share/applications/
    bterminal.desktop into XDG_DESKTOP_DIR before running
    uninstall — without this, sub-test (c) trivially passes
    (no shortcut to remove)."""
    runner = REPO_ROOT / "tools" / "test_wizard_e2e_vm.sh"
    src = runner.read_text()
    fn_idx = src.find("run_mode_uninstall() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "plant-pulpit" in body
    assert "xdg-user-dir DESKTOP" in body
    assert "$HOME/Pulpit" in body  # Polish locale fallback
    assert "cp -f" in body


def test_uninstall_e2e_runner_tests_npm_ai_cli_removal():
    """Pin (d): runner verifies npm-installed claude+copilot
    packages are gone after uninstall."""
    runner = REPO_ROOT / "tools" / "test_wizard_e2e_vm.sh"
    src = runner.read_text()
    fn_idx = src.find("run_mode_uninstall() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "@anthropic-ai" in body
    assert "@github" in body
    assert ".npm-global/bin/claude" in body
    assert ".npm-global/bin/copilot" in body


def test_uninstall_e2e_runner_runs_purge_subtest():
    """Pin (b): runner re-installs after first uninstall,
    then runs `./install.sh --uninstall --purge` and asserts
    user data also removed."""
    runner = REPO_ROOT / "tools" / "test_wizard_e2e_vm.sh"
    src = runner.read_text()
    fn_idx = src.find("run_mode_uninstall() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "--uninstall --purge" in body
    assert ".config/bterminal" in body
    assert ".claude-context" in body


def test_uninstall_e2e_runner_tests_wizard_summary_marker():
    """Pin (e): runner spawns wizard in install mode, picks
    Uninstall radio, advances to summary, and verifies the
    completion marker (`Uninstall completed`) appears in
    install.log or wizard's stdout log."""
    runner = REPO_ROOT / "tools" / "test_wizard_e2e_vm.sh"
    src = runner.read_text()
    fn_idx = src.find("run_mode_uninstall() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    # Wizard is spawned (no --uninstall flag this time)
    assert "/tmp/wiz-uninstall.log" in body
    # Down arrow + Alt+I + Return sequence for radio + license + advance
    assert 'xkey "Down Down"' in body
    assert 'xkey "alt+i"' in body
    # Marker check
    assert "Uninstall completed" in body


def test_install_sh_uninstall_emits_completion_marker_in_log():
    """Pin: install.sh's do_uninstall writes a definitive
    completion marker to install.log (or /tmp fallback when
    --purge wiped CONFIG_DIR) so automated tests can detect
    success without parsing wizard subprocess stdout."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_uninstall() {")
    fn_end = src.find("\ndo_fix()", fn_idx)
    body = src[fn_idx:fn_end]
    # Either explicit log_line OR status_json completion
    assert "Uninstall completed" in body
    # Both success paths must emit final status_json
    assert "status_json done ok 100" in body


# ─── detect_install_state extended (task #143) ────────────────────


def test_detect_install_state_accepts_home_override():
    """Pin: detect_install_state(home=...) lets unit tests pass
    a tmp_path baseline. Without this, every break scenario test
    would have to monkey-patch os.path.expanduser."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def detect_install_state")
    fn_end = src.find("\n\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "home: Optional[Path] = None" in body
    assert "home or Path(os.path.expanduser" in body


def test_detect_install_state_checks_companion_cli_symlinks():
    """Pin (e): detect_install_state classifies missing
    ~/.local/bin/{ctx,tasks,consult,memory_wizard,claude_log}
    as 'broken' even when launcher + pkg are present."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def detect_install_state")
    fn_end = src.find("\n\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    for tool in ("ctx", "tasks", "consult", "memory_wizard", "claude_log"):
        assert f'"{tool}"' in body, (
            f"detect_install_state should check companion CLI {tool}"
        )


def test_detect_install_state_checks_ai_cli_stub_marker():
    """Pin (c): detect_install_state reads first 256 bytes of
    claude/copilot binaries and classifies as 'broken' if stub
    markers ('native binary not installed' / 'postinstall did
    not run') appear."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def detect_install_state")
    fn_end = src.find("\n\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "native binary not installed" in body
    assert "postinstall did not run" in body
    assert "os.access" in body and "X_OK" in body


def test_detect_install_state_treats_stale_lockfile_as_broken():
    """Pin (d): install.lock with a known-dead PID → 'broken'
    so wizard offers Fix (which lets install.sh's stale-lock
    auto-recovery clean up and proceed)."""
    wizard = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
    src = wizard.read_text()
    fn_idx = src.find("def detect_install_state")
    fn_end = src.find("\n\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "install.lock" in body
    assert "os.kill" in body  # PID-alive probe via signal 0


def test_break_scenarios_test_file_covers_all_5():
    """Pin (#143): tests/test_break_scenarios.py has at least
    one test case per break scenario (a)-(e)."""
    test_file = REPO_ROOT / "tests" / "test_break_scenarios.py"
    src = test_file.read_text()
    for tag, desc in (
        ("test_break_a_remove_launcher", "(a)"),
        ("test_break_b_remove_pkg_init", "(b)"),
        ("test_break_c_stub_claude", "(c)"),
        ("test_break_d_stale_install_lock", "(d)"),
        ("test_break_e_remove", "(e)"),
    ):
        assert tag in src, f"Missing test for scenario {desc}"


# ─── do_fix flow extended (task #144) ─────────────────────────────


def test_do_fix_emits_FIX_log_marker():
    """Pin (e): do_fix emits `[FIX] ...` lines to install.log
    so users + tests can grep for what was repaired."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert 'log_line "FIX"' in body
    assert "fix_log()" in body
    assert "fixed_count" in body


def test_do_fix_handles_stale_lockfile():
    """Pin (d): do_fix wipes a lockfile whose recorded PID is dead."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "install.lock" in body
    assert "kill -0" in body
    assert 'rm -f "$CONFIG_DIR/install.lock"' in body
    assert "stale install.lock" in body  # FIX log message


def test_do_fix_relinks_launcher_when_source_present():
    """Pin (a): if launcher source script (bterminal-launcher)
    still exists in INSTALL_DIR but ~/.local/bin/bterminal is
    gone, do_fix re-creates the symlink WITHOUT triggering full
    reinstall (in-place repair)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "$INSTALL_DIR/bterminal-launcher" in body
    assert "Restored launcher symlink" in body


def test_do_fix_relinks_companion_clis_in_place():
    """Pin (e): missing ~/.local/bin/{ctx,tasks,...} are
    relinked from $INSTALL_DIR sources without falling through
    to full reinstall."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "ctx tasks consult memory_wizard claude_log" in body
    assert "Restored symlink" in body


def test_do_fix_schedules_reinstall_for_broken_ai_cli():
    """Pin (c): a stub claude/copilot binary triggers the full
    install path (CLAUDE_NEEDS_INSTALL=true / COPILOT_NEEDS_INSTALL=true)
    so npm reinstall runs idempotently."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "CLAUDE_NEEDS_INSTALL=true" in body
    assert "COPILOT_NEEDS_INSTALL=true" in body
    assert "Claude Code broken" in body
    assert "Copilot CLI broken" in body


def test_do_fix_uses_loose_lookup_for_stub_detection():
    """Pin: do_fix uses find_claude_bin_loose / find_copilot_bin_loose
    when strict find returns empty — needed because a stub binary
    without +x is invisible to find_claude_bin (`[[ -x ]]` gate)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "find_claude_bin_loose" in body
    assert "find_copilot_bin_loose" in body


def test_do_fix_falls_through_to_install_when_pkg_missing():
    """Pin (b): missing bterminal/__init__.py triggers the
    fall-through to the normal install path (ACTION='install')."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert 'ACTION="install"' in body
    assert "BTerminal package __init__.py missing" in body
    assert "need_full_install=true" in body


def test_do_fix_emits_completion_marker_with_fix_count():
    """Pin: do_fix's completion marker includes the count of
    in-place fixes applied — so tests + users can verify the
    repair did real work (not a no-op)."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_fix() {")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert "Repair completed" in body
    assert "$fixed_count" in body or "fixed_count" in body


# ─── Open BTerminal button + Pulpit shortcut (2026-05-08) ─────────


def test_main_py_spawns_bterminal_after_install_accepted():
    """Pin (regression for 2026-05-08): user clicks 'Open BTerminal'
    on summary page → wizard returns accepted=True → __main__.py
    must SPAWN the launcher, not just exit. Pre-fix, the wizard
    just closed and the user saw nothing happen."""
    main_py = REPO_ROOT / "bterminal" / "__main__.py"
    src = main_py.read_text()
    # Look at the post-accepted block
    assert "subprocess.Popen" in src or "_sp.Popen" in src, (
        "After accepted=True, must spawn bterminal launcher."
    )
    assert "start_new_session=True" in src, (
        "Must use start_new_session so BT outlives the wizard."
    )
    # Must use the installed launcher
    assert ".local/bin/bterminal" in src or "bterminal\"" in src
    # And NOT spawn after uninstall (BT no longer exists)
    assert 'final_action == "uninstall"' in src or \
        '"uninstall"' in src


def test_main_py_captures_wizard_action_before_destroy():
    """Pin: action attribute must be read BEFORE wizard.destroy()
    — destroy() clears the GTK widget tree + may invalidate Python
    attrs. Reading after destroy is a use-after-free risk."""
    main_py = REPO_ROOT / "bterminal" / "__main__.py"
    src = main_py.read_text()
    final_action_idx = src.find("final_action")
    destroy_idx = src.find("wizard.destroy()")
    assert final_action_idx > 0 and destroy_idx > 0
    assert final_action_idx < destroy_idx, (
        "final_action must be captured BEFORE wizard.destroy()"
    )


def test_install_sh_creates_desktop_shortcut_on_xdg_desktop_dir():
    """Pin (regression for 2026-05-08): install.sh creates the
    `.desktop` file in the user's actual Desktop folder
    (XDG_DESKTOP_DIR — ~/Pulpit on pl_PL, ~/Desktop on en_US)
    so users see the icon on their desktop. Was previously only
    created in ~/.local/share/applications/ (apps menu)."""
    src = INSTALL_SH.read_text()
    # The desktop-entry creation block has a 2nd cp into XDG_DESKTOP_DIR
    desktop_section_start = src.find("Desktop entry created (apps menu)")
    desktop_section_end = src.find("# ─── Summary",
                                     desktop_section_start)
    assert desktop_section_start > 0
    block = src[desktop_section_start:desktop_section_end]
    # Uses xdg-user-dir
    assert "xdg-user-dir DESKTOP" in block
    # Plus locale fallback list
    for path in ("$HOME/Desktop", "$HOME/Pulpit",
                 "$HOME/Bureau", "$HOME/Schreibtisch"):
        assert path in block, f"Missing locale fallback: {path}"
    # Copies + chmod +x (Mint requires +x to launch)
    assert "cp -f" in block and "bterminal.desktop" in block
    assert "chmod +x" in block


def test_install_sh_marks_desktop_file_trusted():
    """Pin: GNOME-based DEs (Mint, Ubuntu) show 'Untrusted launcher'
    warning unless metadata::trusted is set on the .desktop file.
    install.sh runs `gio set ... metadata::trusted true` — without
    it, double-click on the icon shows a confusing prompt instead
    of launching."""
    src = INSTALL_SH.read_text()
    desktop_section_start = src.find("Desktop entry created (apps menu)")
    desktop_section_end = src.find("# ─── Summary",
                                     desktop_section_start)
    block = src[desktop_section_start:desktop_section_end]
    assert "gio set" in block
    assert "metadata::trusted" in block


def test_main_py_propagates_license_acceptance_before_bt_spawn():
    """Pin: after wizard.run_and_install() returns accepted=True
    AND before spawning bterminal, __main__.py writes the
    license_accepted_hash to ~/.config/bterminal/options.json.
    Without this, BT's first-run license check shows the dialog
    again — user already accepted in the wizard."""
    main_py = REPO_ROOT / "bterminal" / "__main__.py"
    src = main_py.read_text()
    assert "license_accepted_hash" in src
    assert "hashlib.sha256" in src or "_hashlib.sha256" in src
    assert "LICENSE.en.md" in src
    assert "options.json" in src


def test_ollama_client_has_daemon_lifecycle_helpers():
    """Pin (#151): ollama_client exposes start_daemon/stop_daemon
    so options dialog can manage the daemon without forcing user
    to open a terminal. start_daemon = Popen detached + 5s poll
    on /api/tags. stop_daemon = SIGTERM owned-by-current-user
    `ollama serve` PIDs."""
    src = (REPO_ROOT / "bterminal" / "ollama_client.py").read_text()
    assert "def start_daemon" in src
    assert "def stop_daemon" in src
    assert "subprocess as _sp" in src or "import subprocess" in src
    # Detached spawn (start_new_session=True) so daemon outlives BT
    assert "start_new_session=True" in src
    # Poll loop
    assert "is_daemon_running()" in src
    # Don't kill root's systemd unit
    assert 'pgrep' in src and '-u' in src and 'getuid' in src


def test_options_dialog_has_ollama_start_stop_buttons():
    """Pin: options.py Local Models section now has Start/Stop/
    Refresh buttons + status label that auto-updates after click."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    fn_idx = src.find("def _lazy_build_local_models")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "_daemon_btn_start" in body
    assert "_daemon_btn_stop" in body
    assert "_daemon_btn_refresh" in body
    assert "Start daemon" in body and "Stop daemon" in body
    # Old text instruction must be gone (still present in not-installed
    # branch but only as `--selected llama` hint, not "ollama serve &")
    assert "ollama serve &amp;" not in body, (
        "Old 'open a terminal' instruction must be replaced with"
        " in-UI Start button"
    )


def test_options_dialog_refreshes_status_after_action():
    """Pin: clicking Start/Stop triggers _refresh_daemon_status
    which polls is_daemon_running and updates the label."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    assert "_refresh_daemon_status" in src
    # Both handlers call refresh after the daemon op
    for handler in ("_on_ollama_start_clicked",
                    "_on_ollama_stop_clicked"):
        fn_idx = src.find(f"def {handler}")
        fn_end = src.find("\n    def ", fn_idx + 1)
        body = src[fn_idx:fn_end]
        assert "_refresh_daemon_status" in body, (
            f"{handler} must call _refresh_daemon_status"
        )


def test_options_dialog_wraps_content_in_scrolled_window():
    """Task #152: expanding 'AI Providers' + 'Local Models' must not
    push Save/Cancel below the screen edge. The fix wraps the entire
    sectioned content area in a Gtk.ScrolledWindow."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    init_idx = src.find("def __init__(self, parent):")
    # bound to first method that starts with _ (helper) AFTER __init__.
    init_end = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:init_end]
    assert "Gtk.ScrolledWindow()" in init_body, \
        "OptionsDialog must instantiate a ScrolledWindow"
    assert "PolicyType.AUTOMATIC" in init_body, \
        "ScrolledWindow needs AUTOMATIC scroll policy (both axes)"
    assert "set_min_content_height" in init_body, \
        "Min content height must be set so the bar appears reliably"
    # Critical: propagate_natural_height MUST be False — otherwise
    # the ScrolledWindow grows to fit children and the cap is lost.
    assert "set_propagate_natural_height(False)" in init_body, (
        "propagate_natural_height must be False to enforce cap"
    )


def test_options_dialog_caps_height_to_screen_workarea():
    """Pin #152: dialog default-size height must be derived from the
    monitor workarea (≤80% of screen_h) so it never overflows the
    screen, regardless of screen resolution / HiDPI."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    init_idx = src.find("def __init__(self, parent):")
    init_end = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:init_end]
    assert "get_workarea" in init_body, \
        "Must query Gdk monitor.get_workarea() for screen height"
    assert "screen_h * 0.8" in init_body or "screen_h*0.8" in init_body, (
        "Height cap must be ≤ 80% of monitor workarea"
    )


def test_options_dialog_pins_resize_toplevel_false_on_expanders():
    """#153: collapse-then-expander-disappears bug. Both expanders
    must pin set_resize_toplevel(False) explicitly so collapse never
    triggers a window-shrink that hides Save/Cancel + sibling
    expander."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    init_idx = src.find("def __init__(self, parent):")
    init_end = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:init_end]
    # set_resize_toplevel(False) must be called on BOTH expanders
    count = init_body.count("set_resize_toplevel(False)")
    assert count >= 2, (
        f"Expected ≥2 set_resize_toplevel(False) calls, got {count}"
    )


def test_options_dialog_has_min_size_floor_against_collapse():
    """#153: dialog needs an explicit set_size_request() floor so
    collapse can't shrink it below readable height. Without this,
    GtkExpander's natural-size negotiation can produce a 1-line
    window when both are collapsed."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    init_idx = src.find("def __init__(self, parent):")
    init_end = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:init_end]
    assert "self.set_size_request(" in init_body, (
        "Dialog must call set_size_request() to floor min height"
    )


def test_install_sh_passes_bash_syntax_check():
    """Catch syntax errors before they hit a real install."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_install_sh_help_returns_zero():
    """Pin: --help still works after refactor."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
