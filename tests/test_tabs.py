"""Smoke test: open + close local tab lifecycle via REST."""


def test_open_local_tab_increments_count(bterminal_process):
    client = bterminal_process.http_client

    # 1. baseline
    n = client.get("/api/state").json()["tabs_count"]

    # 2. open new local tab
    open_resp = client.post("/api/tabs/local")
    assert open_resp.status_code == 200, open_resp.text
    new_idx = open_resp.json()["idx"]
    assert isinstance(new_idx, int)

    # 3. count went up by exactly one
    assert client.get("/api/state").json()["tabs_count"] == n + 1

    # 4. close it
    close_resp = client.post(f"/api/tabs/{new_idx}/close")
    assert close_resp.status_code == 200, close_resp.text

    # 5. count back to baseline
    assert client.get("/api/state").json()["tabs_count"] == n
