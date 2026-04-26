"""Per-tab intro-prompt sidecar gating (Etap 9)."""


def _close_in_reverse(client, indices):
    for idx in sorted(indices, reverse=True):
        client.post(f"/api/tabs/{idx}/close")


def test_intro_includes_only_tab_enabled_sidecars(bterminal_process):
    """Tab A enables test_sleeper → intro mentions it. Tab B has no
    sidecars enabled → intro does NOT mention it."""
    c = bterminal_process.http_client
    a = c.post("/api/tabs/local").json()["idx"]
    b = c.post("/api/tabs/local").json()["idx"]
    try:
        c.put(f"/api/tabs/{a}/plugins", json={"enabled": ["test_sleeper"]})
        c.put(f"/api/tabs/{b}/plugins", json={"enabled": []})

        prompt_a = c.get(f"/api/tabs/{a}/intro_prompt").json()["intro_prompt"]
        prompt_b = c.get(f"/api/tabs/{b}/intro_prompt").json()["intro_prompt"]

        # A includes the seeded sidecar's section (title from manifest).
        assert "TestSleeper" in prompt_a
        # B does not mention it at all.
        assert "TestSleeper" not in prompt_b
    finally:
        _close_in_reverse(c, [a, b])
