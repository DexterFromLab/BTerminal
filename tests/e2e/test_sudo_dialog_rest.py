"""BUG#31g — REST-driven sudo dialog smoke (component layer).

Exercises the /api/debug/sudo_state + /api/debug/sudo_submit pair on a
live BTerminal subprocess. The conftest session fixture sets
``BTERMINAL_TEST_FAKE_SUDO=1`` so the cache can be populated through
REST without granting the test runner real root.

Scenarios:
  1. Endpoints respond + require auth.
  2. Initial state has no cached path and no pending dialog.
  3. POST sudo_submit with valid password → has_path: True.
  4. POST sudo_submit with empty password → has_path: False (fake mode
     still rejects empty so the False branch stays covered).
"""

import httpx


def test_sudo_state_endpoint_responds(bterminal_process):
    """GET /api/debug/sudo_state must return the documented shape."""
    resp = bterminal_process.http_client.get("/api/debug/sudo_state")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"has_path", "pending_dialog"}
    assert isinstance(data["has_path"], bool)
    assert isinstance(data["pending_dialog"], bool)


def test_sudo_state_requires_auth(bterminal_process):
    """Debug surface must reject requests without the bearer token."""
    naked = httpx.Client(base_url=bterminal_process.base_url, timeout=2.0)
    try:
        resp = naked.get("/api/debug/sudo_state")
        assert resp.status_code == 401
    finally:
        naked.close()


def test_sudo_submit_populates_cache(bterminal_process):
    """POST sudo_submit with a non-empty password → cache is set."""
    # Start clean — every other test may have populated the cache, so
    # the assertion is on the post-submit state, not the pre.
    resp = bterminal_process.http_client.post(
        "/api/debug/sudo_submit", json={"password": "fake-component-test-pw"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["has_path"] is True

    # State endpoint should reflect the same thing on the next poll.
    state = bterminal_process.http_client.get("/api/debug/sudo_state").json()
    assert state["has_path"] is True
    assert state["pending_dialog"] is False, (
        "submit clears the pending flag so spawn flows can resume"
    )


def test_sudo_submit_rejects_empty_password(bterminal_process):
    """Empty password should not silently 'succeed' even in fake mode —
    the False branch is what protects the spawn flow from treating an
    accidental blank as a valid sudo."""
    resp = bterminal_process.http_client.post(
        "/api/debug/sudo_submit", json={"password": ""}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is False
    assert data["has_path"] is False


def test_sudo_submit_requires_auth(bterminal_process):
    """POST endpoint also gated by the bearer token."""
    naked = httpx.Client(base_url=bterminal_process.base_url, timeout=2.0)
    try:
        resp = naked.post(
            "/api/debug/sudo_submit", json={"password": "x"}
        )
        assert resp.status_code == 401
    finally:
        naked.close()
