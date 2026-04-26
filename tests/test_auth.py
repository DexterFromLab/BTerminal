"""Smoke test: bearer-token gating on debug-REST."""

import httpx


def test_no_token_returns_401(bterminal_process):
    resp = httpx.get(f"{bterminal_process.base_url}/api/health")
    assert resp.status_code == 401, resp.text


def test_wrong_token_returns_401(bterminal_process):
    resp = httpx.get(
        f"{bterminal_process.base_url}/api/health",
        headers={"Authorization": "Bearer xxx-not-a-real-token"},
    )
    assert resp.status_code == 401, resp.text


def test_correct_token_works(bterminal_process):
    resp = httpx.get(
        f"{bterminal_process.base_url}/api/health",
        headers={"Authorization": f"Bearer {bterminal_process.token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
