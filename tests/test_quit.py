"""Smoke test: /api/quit gating + destructive-action confirmation."""

import pytest


def test_quit_without_confirm_returns_400(bterminal_process):
    """POST /api/quit without ?confirm=true must be refused; process stays up."""
    resp = bterminal_process.http_client.post("/api/quit")
    assert resp.status_code == 400, resp.text
    assert "confirm" in resp.json()["error"].lower()

    # Process still alive — health still answers
    health = bterminal_process.http_client.get("/api/health")
    assert health.status_code == 200, health.text


@pytest.mark.skip(
    reason=(
        "Destructive — would kill the session-scoped fixture and break "
        "remaining tests. The exact same path is exercised by the session "
        "teardown in conftest.py, which posts /api/quit?confirm=true and "
        "verifies the process exits within QUIT_TIMEOUT_SEC."
    )
)
def test_quit_with_confirm_kills_process(bterminal_process):
    pass
