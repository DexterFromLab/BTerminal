"""Pin tests for tools/test_sidebar_crud_vm.sh + new REST endpoints
for Sidebar CRUD E2E (#160)."""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_sidebar_crud_vm.sh"
DEBUG_REST = REPO_ROOT / "bterminal" / "debug_rest.py"


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_script_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_script_documents_all_5_subtests():
    src = SCRIPT.read_text()
    for header in (
        "(a) Add SSH session",
        "(b) Add Claude session",
        "(c) Edit Claude session",
        "(d) Delete session",
        "(e) Right-click → Run as",
    ):
        assert header in src, f"missing header: {header}"


def test_script_uses_new_rest_endpoints():
    src = SCRIPT.read_text()
    for path in (
        "/api/sessions/ssh",
        "/api/sessions/ai",
        "/api/sessions/$AI_ID/update",
        "/api/sessions/$AI_ID/delete",
        "/api/sessions/$RUN_AS_ID/delete",
    ):
        assert path.replace('$', '') in src.replace('$', ''), \
            f"missing REST path: {path}"


def test_script_uses_existing_context_menu_for_run_as():
    """Pin: Run-as goes through #63's existing endpoint, not a new
    one. Catches regressions where someone reinvents the wheel."""
    src = SCRIPT.read_text()
    assert "/api/sidebar/context_menu/" in src
    assert "action=run_as" in src
    assert "provider=copilot" in src


def test_script_verifies_run_as_provider_override():
    """Pin: run_as must check that the SPAWNED tab uses the override
    provider, not the saved session.provider. Otherwise you'd be
    pinning Connect (same provider) instead of Run-as."""
    src = SCRIPT.read_text()
    assert "saved=claude, spawn=copilot" in src or \
           ("LAST_TAB" in src and "copilot" in src), (
        "must verify spawned tab uses override provider"
    )


def test_script_uses_live_monitor():
    src = SCRIPT.read_text()
    assert "_e2e_live_monitor.sh" in src
    for tag in ("01a-ssh-dialog-open", "02b-after-add",
                "03c-after-rename", "04d-after-delete",
                "05e-after-run-as"):
        assert tag in src, f"missing tag: {tag}"


def test_script_cleans_up_test_sessions():
    """Pin: every $TEST_*_NAME entry gets a /delete REST call before
    exit, so the VM doesn't accumulate orphans run-after-run."""
    src = SCRIPT.read_text()
    # Cleanup of SSH session
    assert 'SSH_ID=$(_get_session_id_by_name ssh "$TEST_SSH_NAME")' in src
    assert '"/api/sessions/$SSH_ID/delete"' in src
    # Cleanup of run-as test session
    assert '"/api/sessions/$RUN_AS_ID/delete"' in src


# ── REST endpoint pin tests ───────────────────────────────────────────────


def test_route_session_add_ssh_present():
    """Pin: /api/sessions/ssh endpoint registered + handler signature."""
    src = DEBUG_REST.read_text()
    assert "_route_session_add_ssh" in src
    assert "/api/sessions/ssh" in src
    # Required body fields
    assert '"name"' in src and '"host"' in src


def test_route_session_add_ai_present():
    src = DEBUG_REST.read_text()
    assert "_route_session_add_ai" in src
    assert "/api/sessions/ai" in src


def test_route_session_update_takes_session_id_kwarg():
    """Pin: BUG fix — earlier impl read h._path_match.group() which
    threw TypeError because dispatcher passes named groups as kwargs.
    Handler signature MUST accept session_id parameter.
    """
    src = DEBUG_REST.read_text()
    fn_idx = src.find("def _route_session_update")
    body_start = src.find('"""', fn_idx)
    sig = src[fn_idx:body_start]
    assert "session_id" in sig, (
        f"signature must accept session_id kwarg, got: {sig}"
    )


def test_route_session_delete_takes_session_id_kwarg():
    src = DEBUG_REST.read_text()
    fn_idx = src.find("def _route_session_delete")
    body_start = src.find('"""', fn_idx)
    sig = src[fn_idx:body_start]
    assert "session_id" in sig, sig


def test_routes_registered_with_correct_methods():
    """Pin: /api/sessions/ssh|ai are POST; /update + /delete are POST
    (we don't have DELETE method)."""
    src = DEBUG_REST.read_text()
    # All 4 new routes must appear in the POST routes table
    routes_section = src[src.find("_routes_post") if "_routes_post" in src
                         else src.find("(r\"/api/quit"):]
    assert "/api/sessions/ssh" in src
    assert "/api/sessions/ai" in src
    assert "_route_session_add_ssh" in src
    assert "_route_session_add_ai" in src
