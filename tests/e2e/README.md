# BTerminal E2E Tests

Tests in this dir use the **vte_capture** fixture + **mock_ai_cli** to
exercise full BTerminal flows end-to-end without requiring a real AI
CLI binary (claude/copilot/aider).

## Foundation

### `vte_capture` fixture

Function-scoped, polls `/api/debug/feed_log` REST endpoint. Captures
bytes BTerminal sends to AI CLI subprocesses, labeled by call site:

- `intro_prompt` — initial prompt fed to spawn'd subprocess (R11)
- `auto_trigger` — task auto-trigger `[AUTO-TRIGGER]` message (R21)
- `rules_inject` — periodic rules re-injection (R15)
- `ctx_refresh` — ctx refresh follow-up message
- `macro` — SSH macro step (future)

### `mock_ai_cli` binary (`tools/mock_ai_cli`)

Provider-agnostic scripted state machine. Reads scenario JSON, loops
on stdin, writes scripted responses, logs everything to
`MOCK_AI_CLI_LOG` (default `/tmp/mock_ai_cli_<pid>.log`).

Scenario format:
```json
{
  "responses": [
    {"trigger": "regex", "reply": "text", "delay_ms": 100},
    ...
  ],
  "default_reply": "fallback when no match",
  "exit_on": "regex matching exit signal"
}
```

Example scenarios in `tests/scenarios/`.

## Pattern: writing a new E2E test

```python
def test_some_flow(bterminal_process, vte_capture):
    # 1. Pivot is set automatically by fixture — clean slate.
    assert vte_capture.events_for() == []

    # 2. Trigger BTerminal action (open tab, run macro, etc.)
    bterminal_process.http_client.post("/api/tabs/claude", json={...})

    # 3. Wait for expected event
    intro = vte_capture.wait_for("intro_prompt", timeout=10.0)

    # 4. Assert content
    assert "## Rules" in intro["text"]
    assert intro["text"].count("##") >= 3
```

## Adding mock_ai_cli to a test

To redirect BTerminal's `claude` binary lookup at mock_ai_cli, set up
a tmp HOME with `~/.local/bin/claude → mock_ai_cli` symlink. The
`bterminal_process` fixture's HOME is on `sys.path[0]` so this works
automatically.

(Implementation pending — current foundation tests just verify the
capture/REST infrastructure works. Full mock-driven E2E in next iter.)

## Limitations / TODO

- Currently we don't have a way to seed `claude_sessions.json` in
  conftest fixture (would unblock `test_intro_prompt_structure` full
  flow). Potential fix: add REST endpoint
  `POST /api/sessions/claude/seed` for testing-only.
- `mock_ai_cli` doesn't yet emit JSONL session log (Claude format) —
  needed for stats bar tests (R32).
- Provider abstraction (R10) refactor will need new capability flags
  in mock; design mock for multi-provider from the start.
