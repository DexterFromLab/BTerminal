"""Smoke test: GET /api/health returns the expected payload."""


def test_health_returns_ok(bterminal_process):
    resp = bterminal_process.http_client.get("/api/health")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert "version" in payload
    assert payload["debug_mode"] is True
    assert isinstance(payload["idle_seconds"], int)
