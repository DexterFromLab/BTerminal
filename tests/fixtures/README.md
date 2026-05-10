# Test fixtures

## copilot_events.jsonl (T3.3)

Hand-crafted full GitHub Copilot CLI session log — used by
`test_copilot_stats_reader.py` to verify the events.jsonl parser
without requiring an active Copilot subscription.

Shape based on:
- [GitHub Docs — Using Copilot CLI session data (chronicle)](https://docs.github.com/en/copilot/how-tos/copilot-cli/chronicle)
- [Concepts — Copilot CLI session data](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/chronicle)
- [jonmagic — GitHub Copilot Session Search and Resume CLI](https://jonmagic.com/posts/github-copilot-session-search-and-resume-cli/)

29 events, 11 `tool.execution_complete` (token-bearing), 1 `tool.execution_failed`,
2 `prompt.user`, 2 `prompt.assistant`, plus session.start/shutdown bookends.

Token totals (sum across the 11 complete events):
- input: 2790
- output: 1230
- cache_read: 880
- cache_write: 400

`session.shutdown.modelMetrics["claude-sonnet-4-5"].requests.cost = 0.0875` —
the canonical cost the reader returns when shutdown is present.

**TODO(L2):** replace with a real `events.jsonl` from a live Copilot
session once a subscription is available. The current fixture is
schema-correct but the exact field names (camelCase vs snake_case),
nested key positions, and decimal precision are best-effort estimates
from public docs. CopilotStatsReader handles both naming conventions
defensively (`_process_event`).

## copilot_events_partial.jsonl

Active-session snapshot — same schema, but the log is truncated before
`session.shutdown`. Used to verify the cost fallback path
(pricing-based estimate via `_COPILOT_PRICING_DEFAULT` Sonnet rates)
when no canonical cost record has been written yet.

Token totals:
- input: 430 (250 + 180)
- output: 230 (180 + 50)
- cache_read: 40
- cache_write: 0

Estimated cost @ Sonnet 4.5 rates: see `test_partial_session_cost_uses_pricing_estimate`.
