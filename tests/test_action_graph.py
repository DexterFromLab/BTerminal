"""Drive every scenario from tests/action_graph/scenarios.json against a
live BTerminal --debug-rest. Each scenario becomes one parameterized test.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.action_graph.runner import GRAPH_DIR, SCENARIOS_PATH, Runner

SNAPSHOT_DIR = Path("/tmp/action-graph-snapshots")


def _scenario_names() -> list[str]:
    return [s["name"] for s in json.loads(SCENARIOS_PATH.read_text())["scenarios"]]


@pytest.fixture(scope="module", autouse=True)
def _prepare_snapshot_dir():
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    yield
    # Don't auto-clean — kept for inspection between runs.


@pytest.mark.parametrize("scenario", _scenario_names())
def test_walk_scenario(bterminal_process, scenario):
    runner = Runner(
        http_client=bterminal_process.http_client,
        base_url=bterminal_process.base_url,
        token=bterminal_process.token,
    )
    results = runner.run_scenario(scenario)

    # Persist any captured screenshots to a stable location keyed by
    # scenario + step index so they can be inspected after the run.
    for i, r in enumerate(results):
        if r.screenshot_path:
            dest = SNAPSHOT_DIR / f"{scenario}-{i:02d}-{r.action_id}.png"
            try:
                shutil.copy(r.screenshot_path, dest)
            except OSError:
                pass

    # Soft sanity: at least one non-skipped step (a scenario that skips
    # everything is almost certainly a misconfiguration).
    non_skipped = [r for r in results if not r.skipped]
    assert non_skipped, f"scenario {scenario}: every step skipped"
