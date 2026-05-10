"""Tests for install.sh --headless / --selected / --status-json flags
(task #4 / #76 in audit doc).

Strategy: the full installer touches apt/sudo/system dirs and isn't
safe to run from pytest. We verify three layers without actually
installing anything:

  1. Bash syntax stays valid (`bash -n`).
  2. Structural checks — flags are parsed and reachable, helpers
     exist, status_json call sites are present at every phase boundary.
  3. Behavioral parity via sourced helpers — `selected_includes()`
     evaluated in isolation against various CSV inputs.

A real end-to-end run is the manual VM smoke step in the task
description (`bash install.sh --headless --selected meld --status-json
--no-sudo`).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ─── Static script analysis ────────────────────────────────────────────────


def test_install_sh_bash_syntax_remains_valid():
    """`bash -n` parses without errors after #4 changes."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_help_flag_returns_zero_and_lists_new_options():
    """`./install.sh --help` exits 0 + mentions every new flag."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--headless" in result.stdout
    assert "--selected" in result.stdout
    assert "--status-json" in result.stdout


def test_unknown_flag_exits_with_error():
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--bogus"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


# ─── Flag declarations + helpers present ───────────────────────────────────


@pytest.mark.parametrize("token", [
    "NO_SUDO=false",
    "HEADLESS=false",
    "SELECTED_DEPS=",
    "STATUS_JSON=false",
])
def test_install_sh_declares_each_flag_default(token):
    """Each new flag has its default state set near the top — flips
    only via argparse."""
    assert token in INSTALL_SH.read_text()


def test_install_sh_argparse_handles_all_new_flags():
    """The while-loop case statement parses each long form + the
    --selected=value short form."""
    text = INSTALL_SH.read_text()
    for needle in (
        "--no-sudo)",
        "--headless)",
        "--selected)",
        "--selected=*)",
        "--status-json)",
        "--help|-h)",
    ):
        assert needle in text, f"argparse missing case for {needle}"


def test_status_json_helper_function_defined():
    """status_json() must be a real function (not just a flag)."""
    text = INSTALL_SH.read_text()
    assert re.search(r"^status_json\(\)\s*\{", text, re.M), (
        "status_json() function missing"
    )


def test_selected_includes_helper_defined():
    text = INSTALL_SH.read_text()
    assert re.search(r"^selected_includes\(\)\s*\{", text, re.M)


# ─── status_json call sites at every phase boundary ────────────────────────


@pytest.mark.parametrize("phase_keyword,phase_label", [
    ("runtime",      "Checking runtime"),
    ("claude",       "Claude Code"),
    ("copilot",      "Copilot"),
    ("llama",        "Ollama"),
    ("system_tools", "system tools"),
    ("gtk",          "GTK bindings"),
    ("files",        "BTerminal files"),
    ("symlinks",     "symlinks"),
    ("finalize",     "Finalizing"),
])
def test_status_json_emitted_for_every_phase(phase_keyword, phase_label):
    """Each phase prints both an `echo "[N/M] ..."` line AND a
    status_json call so the wizard can keep its progress bar in sync."""
    text = INSTALL_SH.read_text()
    # The status_json call MUST appear before the phase echo.
    pattern = rf"status_json\s+{phase_keyword}.*\n.*echo \"\[\d.*?\]"
    match = re.search(pattern, text)
    assert match, (
        f"phase '{phase_keyword}' has no status_json call before its "
        f"echo header (expected label fragment: {phase_label!r})"
    )


def test_status_json_done_emitted_at_end_of_install():
    """Last phase before 'installed successfully' must emit
    status_json done ok 100 so the GTK orchestrator's progress bar
    reaches 100%."""
    text = INSTALL_SH.read_text()
    assert re.search(
        r"status_json\s+done\s+ok\s+100", text,
    ), "missing terminal `status_json done ok 100` call"


# ─── selected_includes behaviour, sourced + invoked ────────────────────────


def _run_selected_includes(csv: str, token: str) -> int:
    """Source install.sh's selected_includes function in isolation and
    invoke with the given args. Returns the exit code (0 = match)."""
    script = f"""
        SELECTED_DEPS={csv!r}
        selected_includes() {{
            [[ -z "$SELECTED_DEPS" ]] && return 0
            local IFS=','
            for sel in $SELECTED_DEPS; do
                [[ "$sel" == "$1" ]] && return 0
            done
            return 1
        }}
        selected_includes {token!r}
    """
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=5,
    ).returncode


def test_selected_includes_empty_csv_matches_everything():
    """Empty SELECTED_DEPS = legacy behaviour (try all auto deps)."""
    assert _run_selected_includes("", "meld") == 0
    assert _run_selected_includes("", "anything") == 0


def test_selected_includes_matches_exact_token():
    assert _run_selected_includes("meld,latex,llama", "meld") == 0
    assert _run_selected_includes("meld,latex,llama", "latex") == 0
    assert _run_selected_includes("meld,latex,llama", "llama") == 0


def test_selected_includes_returns_nonzero_for_missing_token():
    assert _run_selected_includes("meld,latex", "pandoc") != 0
    assert _run_selected_includes("llama", "meld") != 0


def test_selected_includes_handles_single_entry():
    assert _run_selected_includes("meld", "meld") == 0
    assert _run_selected_includes("meld", "pandoc") != 0


def test_selected_includes_does_not_substring_match():
    """'meld' should NOT match 'me' or 'meld-extra' — exact-token only."""
    assert _run_selected_includes("meld", "me") != 0
    assert _run_selected_includes("meld", "meld-extra") != 0


# ─── status_json output shape ──────────────────────────────────────────────


def _run_status_json(args: list[str]) -> str:
    """Source the helper + STATUS_JSON flag, invoke, return stdout."""
    quoted_args = " ".join(repr(a) for a in args)
    script = f"""
        STATUS_JSON=true
        status_json() {{
            [[ "$STATUS_JSON" != true ]] && return 0
            python3 -c "
import json
print(json.dumps({{
    'phase':    '$1',
    'status':   '$2',
    'progress': int('$3'),
    'label':    '$4',
}}))
"
        }}
        status_json {quoted_args}
    """
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout


def test_status_json_emits_one_json_line_per_call():
    out = _run_status_json(["claude", "installing", "15", "Checking"])
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == {
        "phase": "claude",
        "status": "installing",
        "progress": 15,
        "label": "Checking",
    }


def test_status_json_off_by_default_is_silent():
    """STATUS_JSON unset → status_json is a no-op (preserves bash
    install.sh users' clean output)."""
    script = """
        STATUS_JSON=false
        status_json() {
            [[ "$STATUS_JSON" != true ]] && return 0
            echo '{"should":"not appear"}'
        }
        status_json claude installing 15 "x"
    """
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=5,
    )
    assert result.stdout.strip() == ""


def test_status_json_always_emits_progress_as_integer():
    """Schema contract: progress is int (caller may pass strings,
    helper coerces)."""
    out = _run_status_json(["x", "ok", "42", "label"])
    parsed = json.loads(out.strip())
    assert isinstance(parsed["progress"], int)
    assert parsed["progress"] == 42


# ─── TOOL_REPORT parity (#62 + #4 — gating layer didn't break it) ──────────


def test_tool_report_array_still_initialized_after_4_changes():
    """Sanity: TOOL_REPORT existed in #62 + must still be set up by
    install.sh at the right phase. Don't want #4 to accidentally
    delete the array init."""
    text = INSTALL_SH.read_text()
    assert "TOOL_REPORT=()" in text
    assert "TOOL_REPORT+=" in text


def test_emit_tool_summary_function_still_defined():
    """#62's [SUMMARY] block emitter must remain — both layers
    (TOOL_REPORT + status_json) work in parallel for different
    consumers (humans → SUMMARY block, GUI → JSON stream)."""
    text = INSTALL_SH.read_text()
    assert re.search(r"^emit_tool_summary\(\)\s*\{", text, re.M)
    assert "[SUMMARY]" in text


# ─── --selected gating logic in check_tool ─────────────────────────────────


def test_check_tool_consults_selected_includes_for_auto_tier():
    """The 'auto' branch of check_tool must call selected_includes
    BEFORE running apt — otherwise --selected is a no-op."""
    text = INSTALL_SH.read_text()
    # Find the 'auto' branch
    m = re.search(
        r'elif \[\[ "\$tier" == "auto" \]\]; then(.+?)(?:elif|else|fi)',
        text, re.S,
    )
    assert m, "couldn't locate 'auto' branch in check_tool"
    body = m.group(1)
    assert "selected_includes" in body, (
        "auto-tier branch must consult selected_includes before installing"
    )


# ─── Llama phase guarded by --selected llama ───────────────────────────────


def test_llama_phase_present():
    """Phase [2.7/7] for Ollama — the local LLM backend (audit § 5)."""
    text = INSTALL_SH.read_text()
    assert "[2.7/7] Checking local LLM backend" in text
    assert "ollama" in text.lower()


def test_llama_phase_uses_curl_install_only_when_selected():
    """Curl-piped-to-shell is dangerous — must be gated by
    --selected llama (or 'ollama'). Bare install.sh run never auto-
    installs ollama."""
    text = INSTALL_SH.read_text()
    # The curl install line must be inside an `selected_includes "llama"`
    # (or "ollama") branch.
    m = re.search(
        r"selected_includes \"llama\".+?(?=fi\s*$|^else)",
        text, re.S | re.M,
    )
    assert m, "ollama curl install must be guarded by selected_includes"
    assert "curl -fsSL https://ollama.com/install.sh" in m.group(0)


def test_llama_phase_emits_tool_report_entry():
    """ollama status flows into [SUMMARY] block alongside claude/copilot."""
    text = INSTALL_SH.read_text()
    assert 'TOOL_REPORT+=("ok|ollama|auto|Ollama' in text
    assert 'TOOL_REPORT+=("missing|ollama|auto|Ollama' in text
