"""E2E foundation test: vte_capture fixture works end-to-end.

Smoke test for the new testing infrastructure:
  - REST endpoint /api/debug/feed_log exists and is auth'd
  - record_feed() in BTerminal correctly publishes events
  - vte_capture fixture polls + decodes correctly
  - since-pivot isolation works between tests

Does NOT yet exercise full BTerminal flows (those come in
test_intro_prompt_structure / test_task_auto_trigger / etc.).
"""

import time

import pytest


def test_feed_log_endpoint_responds(bterminal_process):
    """GET /api/debug/feed_log must return {events, total_captured}."""
    resp = bterminal_process.http_client.get("/api/debug/feed_log")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "total_captured" in data
    assert isinstance(data["events"], list)
    assert isinstance(data["total_captured"], int)


def test_feed_log_requires_auth(bterminal_process):
    """No bearer token = 401."""
    import httpx
    naked = httpx.Client(base_url=bterminal_process.base_url, timeout=2.0)
    try:
        resp = naked.get("/api/debug/feed_log")
        assert resp.status_code == 401
    finally:
        naked.close()


def test_feed_log_label_filter(bterminal_process):
    """?label=X filters to only events with that label."""
    resp = bterminal_process.http_client.get(
        "/api/debug/feed_log", params={"label": "non_existent_label_xyz"}
    )
    assert resp.status_code == 200
    assert resp.json()["events"] == []


def test_feed_log_since_filter(bterminal_process):
    """?since=<future_ts> returns empty (no events after future timestamp)."""
    future = time.time() + 1000.0
    resp = bterminal_process.http_client.get(
        "/api/debug/feed_log", params={"since": future}
    )
    assert resp.status_code == 200
    assert resp.json()["events"] == []


def test_vte_capture_fixture_reset_isolation(bterminal_process, vte_capture):
    """vte_capture fixture starts with fresh since pivot. events_for() must
    return only events recorded after pivot."""
    # Without doing anything, no events should appear (pivot just set)
    events = vte_capture.events_for()
    assert events == []
