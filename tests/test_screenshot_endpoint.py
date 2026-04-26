"""GET /api/window/screenshot returns a real, openable PNG of the window."""

import os

from PIL import Image


def test_screenshot_returns_valid_png(bterminal_process):
    c = bterminal_process.http_client
    payload = c.get("/api/window/screenshot").json()

    assert "path" in payload, payload
    path = payload["path"]
    assert os.path.isfile(path), f"screenshot file missing: {path}"

    # Reported dimensions match the JSON payload.
    assert payload["width"] > 100
    assert payload["height"] > 100

    # PNG actually opens and matches the reported size.
    with Image.open(path) as img:
        assert img.format == "PNG"
        assert img.size == (payload["width"], payload["height"])
