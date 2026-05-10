"""Pin tests for tools/test_ai_spawn_vm.sh — AI spawn E2E (#161)."""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_ai_spawn_vm.sh"


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_script_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_script_tests_all_3_providers():
    src = SCRIPT.read_text()
    for prov in ("claude", "copilot", "aider"):
        assert f"_test_provider {prov}" in src, f"missing: {prov}"


def test_script_tests_spawn_and_close_per_provider():
    """Spec: 6 sub-tests (spawn + close × 3 providers)."""
    src = SCRIPT.read_text()
    # Spawn: POST /api/tabs/ai/$prov
    assert '/api/tabs/ai/$prov' in src
    # Close: POST /api/tabs/$idx/close?force=true
    assert "/close?force=true" in src, (
        "close needs ?force=true because AI spawn registers active task"
    )


def test_script_uses_force_close_flag():
    """Pin: BUG fix — earlier impl called /close without ?force=true,
    got 'tab has active task' refusal for all 3 providers (3 fails).
    AI tabs auto-register a task per spawn; force=true bypasses it."""
    src = SCRIPT.read_text()
    assert "?force=true" in src
    # The comment must explain WHY (so future-me doesn't drop it)
    assert "active task" in src.lower()


def test_script_feeds_echo_hello_per_provider():
    src = SCRIPT.read_text()
    assert 'echo hello' in src
    assert "/api/tabs/$tab_idx/feed" in src


def test_script_screenshots_each_step():
    """Pin: 3 tags per provider (after-spawn / after-feed / after-close)."""
    src = SCRIPT.read_text()
    for tag in ("${prov}-1-after-spawn", "${prov}-2-after-feed",
                "${prov}-3-after-close"):
        assert tag in src, f"missing tag pattern: {tag}"


def test_script_handles_missing_aider_via_mock():
    """Pin: aider binary may be missing on VM (task #77 reinstall pending).
    Script must symlink mock_ai_cli for spawn smoke instead of skipping."""
    src = SCRIPT.read_text()
    assert "mock_ai_cli" in src
    assert "AIDER_SYMLINK_CREATED" in src
    # Cleanup must remove the symlink so the VM stays clean
    assert "rm -f" in src and "aider" in src


def test_script_asserts_no_fatal_log_markers():
    """Pin: log assertion pass = no Tracebacks during full run."""
    src = SCRIPT.read_text()
    assert "FATAL" in src and "Traceback" in src
    assert "no FATAL/Traceback" in src or "FATAL" in src


def test_script_verifies_tab_provider_matches_saved():
    """Pin: spawned tab.provider must match the path-arg provider —
    catches regression where spawn falls back to default provider."""
    src = SCRIPT.read_text()
    assert "_get_tab_provider" in src
    assert "matches saved" in src


def test_script_creates_saved_sessions_for_each_provider():
    """Pin: REST /api/sessions/ai used to seed test fixtures —
    tabs/ai/<prov> requires a saved session by name."""
    src = SCRIPT.read_text()
    assert '"/api/sessions/ai"' in src
    assert 'SESSION_NAMES[claude]' in src
    assert 'SESSION_NAMES[copilot]' in src
    assert 'SESSION_NAMES[aider]' in src


def test_script_cleanup_removes_saved_sessions():
    """Pin: trap EXIT must delete every test session — VM stays clean
    even when test fails midway."""
    src = SCRIPT.read_text()
    # trap EXIT block must iterate sessions and call delete
    assert "trap" in src and "EXIT" in src
    assert "/delete" in src
    # cleanup loop in trap
    assert "for prov in claude copilot aider" in src
