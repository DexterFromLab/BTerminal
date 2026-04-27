"""Random-walk exploration of BTerminal --debug-rest hunting for bugs.

Coverage-biased walker over the action graph. Critical kinds fail the
test (process_dead, exception). Soft kinds (drift, slow_response,
no_valid_actions) are written to the report but do not fail — they are
the interesting findings the run is meant to surface.
"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.action_graph.explorer import Explorer
from tests.action_graph.runner import Runner

REPORT_PATH = Path("/tmp/bterminal-exploration-report.json")
SUMMARY_PATH = Path("/tmp/bterminal-exploration-summary.txt")


def _format_summary(report) -> str:
    lines = [
        "=== BTerminal exploration report ===",
        f"seed:           {report.seed}",
        f"max_steps:      {report.max_steps}",
        f"executed:       {report.steps_executed}",
        f"duration:       {report.duration_s:.2f}s",
        f"anomalies:      {len(report.anomalies)}",
        "",
        "Action coverage:",
    ]
    for aid, n in sorted(report.action_coverage.items(), key=lambda x: -x[1]):
        lines.append(f"  {aid:50s} {n:4d}")
    if report.anomalies:
        lines.append("")
        lines.append("Anomalies:")
        by_kind = {}
        for a in report.anomalies:
            by_kind.setdefault(a["kind"], []).append(a)
        for kind, items in sorted(by_kind.items()):
            lines.append(f"  [{kind}] x{len(items)}")
            for a in items[:3]:  # first 3 of each kind
                lines.append(f"    step {a['step']:3d} {a['action_id']:40s} {a['detail'][:80]}")
            if len(items) > 3:
                lines.append(f"    … +{len(items)-3} more")
    return "\n".join(lines)


@pytest.mark.slow
def test_exploration(bterminal_process):
    runner = Runner(
        http_client=bterminal_process.http_client,
        base_url=bterminal_process.base_url,
        token=bterminal_process.token,
    )
    explorer = Explorer(runner, seed=42, max_steps=1000)
    explorer.rng._seed_for_report = 42  # for the report header

    report = explorer.run()

    REPORT_PATH.write_text(json.dumps({
        "seed": report.seed,
        "max_steps": report.max_steps,
        "steps_executed": report.steps_executed,
        "duration_s": round(report.duration_s, 2),
        "action_coverage": report.action_coverage,
        "anomalies": report.anomalies,
    }, indent=2))
    SUMMARY_PATH.write_text(_format_summary(report))

    print("\n" + _format_summary(report))

    critical = [a for a in report.anomalies
                if a["kind"] in ("process_dead", "process_unreachable", "exception")]
    assert not critical, (
        f"{len(critical)} CRITICAL anomalies (process died or unhandled exception):\n"
        + json.dumps(critical[:5], indent=2)
    )

    # Soft expectation: at least 80% of non-skipped actions should have run
    # at least once over 200 steps (sanity for the coverage heuristic).
    pool_ids = {a["id"] for a in explorer.action_pool}
    covered = set(report.action_coverage)
    coverage_pct = 100 * len(covered) / max(1, len(pool_ids))
    print(f"\nAction coverage: {len(covered)}/{len(pool_ids)} = {coverage_pct:.0f}%")
    assert coverage_pct >= 80, (
        f"Coverage too low: only {coverage_pct:.0f}% of actions executed "
        f"(missed: {sorted(pool_ids - covered)})"
    )
