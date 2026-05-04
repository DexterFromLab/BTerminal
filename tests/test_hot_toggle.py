"""Hot toggle for GTK plugins via REST.

Uses the seeded test_panel plugin from conftest. Verifies the plugin
loaded → disable removes it from app._plugins → enable re-loads it,
all without restarting BTerminal.
"""


def _is_loaded(client, name: str) -> bool:
    plugins = client.get("/api/plugins").json()["plugins"]
    entry = next((p for p in plugins if p["name"] == name), None)
    return bool(entry and entry.get("loaded"))


def test_plugin_hot_disable_removes_from_sidebar(bterminal_process):
    c = bterminal_process.http_client

    # Pre-condition: seeded plugin is loaded after BT startup.
    assert _is_loaded(c, "test_panel") is True

    # Disable: hot unload.
    r = c.post("/api/plugins/test_panel/disable")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["was_loaded"] is True
    assert _is_loaded(c, "test_panel") is False

    # Re-enable: hot load.
    r = c.post("/api/plugins/test_panel/enable")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["already_loaded"] is False
    assert payload["loaded_now"] is True
    assert _is_loaded(c, "test_panel") is True

    # Idempotent enable: already_loaded=True path.
    r = c.post("/api/plugins/test_panel/enable")
    assert r.json()["already_loaded"] is True
