"""Regression: GTK plugin + sidecar manifest coexist with no interference.

On a real installation this is RemoteControll alongside btmsg/explorer/etc.
The fixture seeds test_panel (GTK) + test_sleeper (sidecar) for an
isolated equivalent of that scenario.
"""


def test_gtk_plugin_and_sidecar_coexist(bterminal_process):
    c = bterminal_process.http_client

    plugins = c.get("/api/plugins").json()["plugins"]
    sidecars = c.get("/api/sidecars").json()["sidecars"]

    plugin_by_name = {p["name"]: p for p in plugins}
    sidecar_by_name = {s["name"]: s for s in sidecars}

    # GTK plugin loaded
    assert "test_panel" in plugin_by_name
    assert plugin_by_name["test_panel"]["loaded"] is True

    # Sidecar manifest discovered
    assert "test_sleeper" in sidecar_by_name
    assert sidecar_by_name["test_sleeper"]["running"] is False  # not auto-started

    # No name clash: GTK plugin and sidecar use different namespaces in
    # /api/plugins vs /api/sidecars — same name in both would be the bug.
    assert set(plugin_by_name) & set(sidecar_by_name) == set(), (
        "GTK plugin and sidecar manifest cannot share a name"
    )


def test_gtk_hot_toggle_does_not_affect_sidecars(bterminal_process):
    """Hot disable + enable of a GTK plugin must not perturb sidecar state."""
    c = bterminal_process.http_client

    sidecars_before = c.get("/api/sidecars").json()["sidecars"]
    c.post("/api/plugins/test_panel/disable")
    c.post("/api/plugins/test_panel/enable")
    sidecars_after = c.get("/api/sidecars").json()["sidecars"]

    assert sidecars_before == sidecars_after
