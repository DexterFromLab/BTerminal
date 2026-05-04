"""Pytest fixture for visual regression tests.

The `visual_match` fixture wraps `assert_visual_match` so panel tests
can do:

    def test_consult_panel_visual(bterminal_process, visual_match):
        png = bterminal_process.http_client.get("/api/window/screenshot").content
        visual_match(png, "consult")

To accept new baselines: BTERMINAL_VISUAL_UPDATE=1 pytest tests/visual/
"""

import os
from pathlib import Path

import pytest

from tests.visual.compare import assert_visual_match

VISUAL_DIR = Path(__file__).parent
BASELINE_DIR = VISUAL_DIR / "baseline"


@pytest.fixture
def visual_match(tmp_path):
    """Returns a callable: (png_bytes, name) -> assert match (or save baseline).

    `name` is used both for the baseline filename (`baseline/{name}.png`)
    and for the diff message — keep it stable per-test.
    """
    update = os.environ.get("BTERMINAL_VISUAL_UPDATE") == "1"

    def _match(png_bytes, name, tolerance=None):
        actual = tmp_path / f"{name}-actual.png"
        actual.write_bytes(png_bytes)
        kwargs = {"update": update}
        if tolerance is not None:
            kwargs["tolerance"] = tolerance
        assert_visual_match(actual, BASELINE_DIR, name, **kwargs)

    return _match
