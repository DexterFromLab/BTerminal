"""Per-tab plugin gating + sidecar refcount integration tests.

Uses the seeded test_sleeper sidecar (`sleep 9999`) from conftest. Intro-prompt
content checking is deferred to Etap 9 (separate test file).
"""

import time


def _sleeper_running(client) -> bool:
    sidecars = client.get("/api/sidecars").json()["sidecars"]
    s = next((x for x in sidecars if x["name"] == "test_sleeper"), None)
    return bool(s and s["running"])


def _close_in_reverse(client, indices):
    """Close given indices high-to-low so notebook reindexing doesn't bite."""
    for idx in sorted(indices, reverse=True):
        client.post(f"/api/tabs/{idx}/close")


def test_per_tab_enabled_plugins_isolation(bterminal_process):
    """Two tabs hold independent enabled_plugins assignments."""
    c = bterminal_process.http_client

    a = c.post("/api/tabs/local").json()["idx"]
    b = c.post("/api/tabs/local").json()["idx"]
    try:
        c.put(f"/api/tabs/{a}/plugins", json={"enabled": ["test_sleeper"]})
        c.put(f"/api/tabs/{b}/plugins", json={"enabled": []})

        assert c.get(f"/api/tabs/{a}/plugins").json()["enabled_plugins"] == ["test_sleeper"]
        assert c.get(f"/api/tabs/{b}/plugins").json()["enabled_plugins"] == []
    finally:
        _close_in_reverse(c, [a, b])


def test_sidecar_refcount(bterminal_process):
    """Two tabs reference test_sleeper. Closing one keeps it alive; closing
    the second stops it (refcount 2 → 1 → 0)."""
    c = bterminal_process.http_client

    # Pre-condition: not running
    assert _sleeper_running(c) is False

    a = c.post("/api/tabs/local").json()["idx"]
    b = c.post("/api/tabs/local").json()["idx"]
    try:
        # Both tabs acquire test_sleeper
        c.put(f"/api/tabs/{a}/plugins", json={"enabled": ["test_sleeper"]})
        time.sleep(0.3)  # let runner.start fork
        assert _sleeper_running(c) is True

        c.put(f"/api/tabs/{b}/plugins", json={"enabled": ["test_sleeper"]})
        # Refcount 1 → 2: still running with the same PID; verify alive
        assert _sleeper_running(c) is True

        # Close one: refcount 2 → 1, still alive
        higher = max(a, b)
        c.post(f"/api/tabs/{higher}/close")
        time.sleep(0.3)
        assert _sleeper_running(c) is True

        # Close the other: refcount 1 → 0, stops
        lower = min(a, b)
        c.post(f"/api/tabs/{lower}/close")
        time.sleep(0.5)
        assert _sleeper_running(c) is False
    finally:
        # Defensive cleanup if the test bailed mid-way
        for idx in c.get("/api/tabs").json()["tabs"]:
            if idx["title"] == "Terminal" and idx["idx"] != 0:
                c.post(f"/api/tabs/{idx['idx']}/close")
