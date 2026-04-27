"""Random walker over actions.json that hunts for bugs.

Strategy:
- pick a valid action (precondition met) biased toward least-used → coverage
- execute it, time it, sync abstract state from server periodically
- flag invariants violations (model drift), slow responses, dead process,
  unexpected exceptions, status mismatches

Output: a JSON report with action coverage + every anomaly. The pytest
entry asserts only on critical kinds (process_dead, exception); soft
anomalies (slow_response, drift) are logged but not failed-on.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from tests.action_graph.runner import Runner, _eval_precondition

# Actions skipped in exploration:
# - quit_without_confirm_rejected: already covered, fast and noisy
# - feed_oversized_rejected: massive 100KB body slows the loop
# - key_not_in_whitelist_rejected: covered
# - get_test_sleeper_health_when_stopped: precondition rarely met after
#   exploration starts pumping the sidecar; pytest covers it directly
SKIP_IDS = {
    "quit_without_confirm_rejected",
    "feed_oversized_rejected",
    "key_not_in_whitelist_rejected",
}


@dataclass
class Anomaly:
    step: int
    action_id: str
    kind: str
    detail: str
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None


@dataclass
class ExplorationReport:
    seed: int
    max_steps: int
    steps_executed: int = 0
    action_coverage: dict[str, int] = field(default_factory=dict)
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0


class Explorer:
    def __init__(
        self,
        runner: Runner,
        seed: int = 42,
        max_steps: int = 200,
        slow_threshold_ms: float = 2000.0,
        invariant_check_every: int = 5,
    ):
        self.runner = runner
        self.rng = random.Random(seed)
        self.max_steps = max_steps
        self.slow_threshold_ms = slow_threshold_ms
        self.invariant_check_every = invariant_check_every
        self.action_counts: dict[str, int] = defaultdict(int)
        self.anomalies: list[Anomaly] = []
        self.action_pool = [
            a for a in runner.actions.values()
            if a["id"] not in SKIP_IDS
        ]

    def _select_next(self) -> dict | None:
        """Coverage-biased: pick from least-used quartile of valid actions."""
        candidates = []
        for a in self.action_pool:
            precond = a.get("precondition")
            if precond and not _eval_precondition(precond, self.runner.state):
                continue
            candidates.append(a)
        if not candidates:
            return None
        candidates.sort(key=lambda a: self.action_counts[a["id"]])
        cutoff = max(1, len(candidates) // 3)
        return self.rng.choice(candidates[:cutoff])

    def _check_invariants(self, step_n: int, last_action_id: str) -> None:
        """Sync abstract model with server truth + check resource health.
        Any drift / leak / zombie → anomaly.
        """
        client = self.runner.client
        try:
            srv = client.get("/api/state").json()
        except (httpx.HTTPError, OSError) as exc:
            self.anomalies.append(Anomaly(
                step_n, last_action_id, "process_unreachable",
                f"GET /api/state failed: {type(exc).__name__}: {exc}",
            ))
            return

        m = self.runner.state
        if srv["tabs_count"] != m.tabs_count:
            self.anomalies.append(Anomaly(
                step_n, last_action_id, "drift_tabs_count",
                f"model={m.tabs_count} server={srv['tabs_count']}",
                state_before=m.snapshot(),
            ))
            m.tabs_count = srv["tabs_count"]  # heal

        srv_plugins = set(srv["plugins_loaded"])
        if srv_plugins != m.plugins_loaded:
            self.anomalies.append(Anomaly(
                step_n, last_action_id, "drift_plugins_loaded",
                f"model={sorted(m.plugins_loaded)} server={sorted(srv_plugins)}",
                state_before=m.snapshot(),
            ))
            m.plugins_loaded = srv_plugins  # heal

        try:
            sidecars = client.get("/api/sidecars").json()["sidecars"]
        except (httpx.HTTPError, OSError):
            return
        srv_running = {s["name"] for s in sidecars if s["running"]}
        if srv_running != m.sidecars_running:
            self.anomalies.append(Anomaly(
                step_n, last_action_id, "drift_sidecars_running",
                f"model={sorted(m.sidecars_running)} server={sorted(srv_running)}",
                state_before=m.snapshot(),
            ))
            m.sidecars_running = srv_running  # heal

        # Resource invariants — measured against the BT process directly.
        bt_pid = self._find_bt_pid()
        if bt_pid is None:
            self.anomalies.append(Anomaly(
                step_n, last_action_id, "process_dead",
                "no python3 bterminal.py process found in /proc",
                state_before=m.snapshot(),
            ))
            return

        rss_kb = self._read_rss_kb(bt_pid)
        if rss_kb is not None:
            self._record_rss(step_n, rss_kb)
            growth = self._rss_growth_kb()
            if growth and growth > 50_000:  # > 50 MB growth from baseline
                self.anomalies.append(Anomaly(
                    step_n, last_action_id, "memory_growth",
                    f"RSS grew {growth} KB from baseline (now {rss_kb} KB)",
                    state_before=m.snapshot(),
                ))

        fd_count = self._count_fds(bt_pid)
        if fd_count is not None and fd_count > 200:
            self.anomalies.append(Anomaly(
                step_n, last_action_id, "fd_leak",
                f"{fd_count} open FDs on BT (threshold 200)",
                state_before=m.snapshot(),
            ))

        zombies = self._count_zombies()
        if zombies > 0:
            self.anomalies.append(Anomaly(
                step_n, last_action_id, "zombie_processes",
                f"{zombies} zombie processes in tree",
                state_before=m.snapshot(),
            ))

    def _find_bt_pid(self) -> int | None:
        try:
            import os
            for pid_str in os.listdir("/proc"):
                if not pid_str.isdigit():
                    continue
                try:
                    cmdline = open(f"/proc/{pid_str}/cmdline").read()
                except OSError:
                    continue
                if "bterminal.py" in cmdline and "--debug-rest" in cmdline:
                    return int(pid_str)
        except OSError:
            pass
        return None

    def _read_rss_kb(self, pid: int) -> int | None:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except OSError:
            return None
        return None

    def _record_rss(self, step_n: int, rss_kb: int) -> None:
        if not hasattr(self, "_rss_baseline"):
            self._rss_baseline = rss_kb
        self._rss_last = rss_kb

    def _rss_growth_kb(self) -> int | None:
        if not hasattr(self, "_rss_baseline") or not hasattr(self, "_rss_last"):
            return None
        return self._rss_last - self._rss_baseline

    def _count_fds(self, pid: int) -> int | None:
        try:
            import os
            return len(os.listdir(f"/proc/{pid}/fd"))
        except OSError:
            return None

    def _count_zombies(self) -> int:
        import os
        n = 0
        try:
            for pid_str in os.listdir("/proc"):
                if not pid_str.isdigit():
                    continue
                try:
                    with open(f"/proc/{pid_str}/status") as f:
                        for line in f:
                            if line.startswith("State:") and "Z" in line.split()[1]:
                                n += 1
                                break
                except OSError:
                    continue
        except OSError:
            pass
        return n

    def run(self) -> ExplorationReport:
        report = ExplorationReport(seed=self.rng.seed if False else 0,
                                    max_steps=self.max_steps)
        # Snapshot the requested seed for the report
        report.seed = getattr(self.rng, "_seed_for_report", 0)
        started = time.monotonic()
        self.runner.sync_state_from_server()

        for step_n in range(self.max_steps):
            action = self._select_next()
            if action is None:
                self.anomalies.append(Anomaly(
                    step_n, "(none)", "no_valid_actions",
                    "all preconditions failed — explorer stuck",
                    state_before=self.runner.state.snapshot(),
                ))
                break

            before = self.runner.state.snapshot()
            t0 = time.monotonic()
            try:
                self.runner.step(action["id"])
            except AssertionError as exc:
                self.anomalies.append(Anomaly(
                    step_n, action["id"], "assertion_failed",
                    str(exc), before, None,
                ))
                # Try to keep going — refresh state from server
                try:
                    self.runner.sync_state_from_server()
                except Exception as exc2:
                    self.anomalies.append(Anomaly(
                        step_n, "(sync)", "process_dead",
                        f"cannot resync: {exc2}", before, None,
                    ))
                    break
                continue
            except (httpx.HTTPError, OSError) as exc:
                self.anomalies.append(Anomaly(
                    step_n, action["id"], "process_dead",
                    f"{type(exc).__name__}: {exc}", before, None,
                ))
                break
            except Exception as exc:  # noqa: BLE001
                self.anomalies.append(Anomaly(
                    step_n, action["id"], "exception",
                    f"{type(exc).__name__}: {exc}", before, None,
                ))
                break

            dt_ms = (time.monotonic() - t0) * 1000
            if dt_ms > self.slow_threshold_ms:
                self.anomalies.append(Anomaly(
                    step_n, action["id"], "slow_response",
                    f"{dt_ms:.0f}ms > {self.slow_threshold_ms:.0f}ms",
                    before, self.runner.state.snapshot(),
                ))

            self.action_counts[action["id"]] += 1

            if (step_n + 1) % self.invariant_check_every == 0:
                self._check_invariants(step_n, action["id"])

        report.steps_executed = sum(self.action_counts.values())
        report.action_coverage = dict(self.action_counts)
        report.anomalies = [asdict(a) for a in self.anomalies]
        report.duration_s = time.monotonic() - started
        return report
