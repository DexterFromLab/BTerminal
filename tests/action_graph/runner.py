"""Action graph runner — interprets actions.json + scenarios.json against
a running BTerminal --debug-rest instance.

Used by tests/test_action_graph.py. Standalone entry point also works:
    python -m tests.action_graph.runner [--scenario name]
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

GRAPH_DIR = Path(__file__).parent
ACTIONS_PATH = GRAPH_DIR / "actions.json"
SCENARIOS_PATH = GRAPH_DIR / "scenarios.json"

# Marker substituted in path templates: "{tabs_count-1}" → str(state.tabs_count - 1)
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)([+-]\d+)?\}")


@dataclass
class State:
    """Abstract mirror of BTerminal's runtime state. Updated by actions'
    `effect` field; preconditions are evaluated against it.
    """
    tabs_count: int = 0
    sidebar_visible: bool = True
    git_panel_visible: bool = False
    plugins_loaded: set[str] = field(default_factory=set)
    sidecars_running: set[str] = field(default_factory=set)
    tab_plugins: dict[int, set | None] = field(default_factory=dict)

    def snapshot(self) -> dict:
        return {
            "tabs_count": self.tabs_count,
            "sidebar_visible": self.sidebar_visible,
            "git_panel_visible": self.git_panel_visible,
            "plugins_loaded": sorted(self.plugins_loaded),
            "sidecars_running": sorted(self.sidecars_running),
        }


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _substitute(template: str, state: State) -> str:
    """Resolve {tabs_count-1} style placeholders against state."""
    def repl(m: re.Match) -> str:
        field_name = m.group(1)
        offset = int(m.group(2) or "0")
        return str(getattr(state, field_name) + offset)
    return PLACEHOLDER_RE.sub(repl, template)


def _build_oversized_text() -> dict:
    return {"text": "x" * (100 * 1024)}  # > 64 KB cap


_JSON_FACTORIES = {
    "oversized_text": _build_oversized_text,
}


# Whitelist of builtins exposed to precondition/effect mini-language.
# Keep it tight — only what an `effect` string legitimately needs.
_SAFE_BUILTINS = {"set": set, "list": list, "dict": dict, "len": len}


def _eval_precondition(expr: str, state: State) -> bool:
    """Evaluate `expr` (Python boolean expression) against state attrs."""
    return bool(eval(expr, {"__builtins__": _SAFE_BUILTINS}, state.__dict__))  # noqa: S307


def _apply_effect(effect: str | None, state: State) -> None:
    """Apply `effect` (Python statements) to mutate state."""
    if not effect:
        return
    # Split on `;` for multi-statement effects.
    for stmt in effect.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        # `tabs_count += 1` style needs exec, not eval.
        local = state.__dict__
        exec(stmt, {"__builtins__": _SAFE_BUILTINS}, local)  # noqa: S102
    # exec writes to local dict — pull set/int values back onto the dataclass.
    for k, v in list(state.__dict__.items()):
        setattr(state, k, v)


@dataclass
class StepResult:
    action_id: str
    skipped: bool = False
    skip_reason: str = ""
    status_code: int | None = None
    response_json: Any = None
    screenshot_path: str | None = None


class Runner:
    def __init__(self, http_client: httpx.Client, base_url: str, token: str):
        self.client = http_client          # auth-preconfigured
        self.base_url = base_url
        self.token = token
        self.actions = {a["id"]: a for a in _load(ACTIONS_PATH)["actions"]}
        self.scenarios = {s["name"]: s for s in _load(SCENARIOS_PATH)["scenarios"]}
        self.state = State()
        self.history: list[StepResult] = []

    # ── State seeding ──────────────────────────────────────────────────────

    def sync_state_from_server(self) -> None:
        """Pull truth from the live server into the abstract state. Call
        once at the start of every scenario so steps are evaluated against
        what BTerminal is actually doing right now (not stale state).
        """
        s = self.client.get("/api/state").json()
        self.state.tabs_count = s["tabs_count"]
        self.state.plugins_loaded = set(s["plugins_loaded"])
        # sidebar/git visibility aren't in /api/state — assume defaults.
        sidecars = self.client.get("/api/sidecars").json()["sidecars"]
        self.state.sidecars_running = {
            x["name"] for x in sidecars if x["running"]
        }

    # ── Single step ────────────────────────────────────────────────────────

    def step(self, action_id: str, snapshot: bool = False, **assertions) -> StepResult:
        action = self.actions[action_id]
        result = StepResult(action_id=action_id)

        # Precondition gate
        precond = action.get("precondition")
        if precond and not _eval_precondition(precond, self.state):
            result.skipped = True
            result.skip_reason = f"precondition not met: {precond}"
            self.history.append(result)
            return result

        # Build request
        req = action["request"]
        path = _substitute(req["path"], self.state)
        method = req["method"]
        kwargs: dict = {}
        if "json" in req:
            kwargs["json"] = req["json"]
        elif "json_factory" in req:
            kwargs["json"] = _JSON_FACTORIES[req["json_factory"]]()
        headers = None
        if req.get("no_auth"):
            # Bypass the fixture client's auth header.
            headers = {}  # explicit empty
            resp = httpx.request(method, f"{self.base_url}{path}", **kwargs)
        else:
            resp = self.client.request(method, path, **kwargs)

        result.status_code = resp.status_code
        try:
            result.response_json = resp.json()
        except Exception:
            result.response_json = resp.text

        # Status assertion
        expected_status = action.get("expect_status")
        if expected_status is not None:
            assert resp.status_code == expected_status, (
                f"{action_id}: expected status {expected_status} got "
                f"{resp.status_code} — body: {resp.text[:200]}"
            )

        # Field presence assertion
        if "expect_field" in action:
            assert action["expect_field"] in (result.response_json or {}), (
                f"{action_id}: response missing field "
                f"{action['expect_field']!r}: {result.response_json}"
            )

        # Exact JSON subset assertion
        if "expect_json" in action:
            for k, v in action["expect_json"].items():
                assert result.response_json.get(k) == v, (
                    f"{action_id}: expected {k}={v!r}, got "
                    f"{result.response_json.get(k)!r}"
                )

        # Per-step (scenario-level) assertions
        if "expect_contains" in assertions:
            body_text = json.dumps(result.response_json)
            assert assertions["expect_contains"] in body_text, (
                f"{action_id}: response did not contain "
                f"{assertions['expect_contains']!r}"
            )
        if "expect_not_contains" in assertions:
            body_text = json.dumps(result.response_json)
            assert assertions["expect_not_contains"] not in body_text, (
                f"{action_id}: response unexpectedly contained "
                f"{assertions['expect_not_contains']!r}"
            )

        # Mutate abstract state
        _apply_effect(action.get("effect"), self.state)

        # Optional screenshot for visual self-test
        if snapshot:
            shot = self.client.get("/api/window/screenshot").json()
            result.screenshot_path = shot["path"]

        self.history.append(result)
        return result

    # ── Scenario walk ──────────────────────────────────────────────────────

    def run_scenario(self, name: str) -> list[StepResult]:
        scenario = self.scenarios[name]
        self.sync_state_from_server()
        results: list[StepResult] = []
        for step in scenario["steps"]:
            extras = {k: v for k, v in step.items()
                      if k in ("expect_contains", "expect_not_contains")}
            r = self.step(
                step["action"],
                snapshot=step.get("snapshot", False),
                **extras,
            )
            results.append(r)
        return results
