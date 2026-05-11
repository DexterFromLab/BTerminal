# Task #103 (#175) — Rules injection verify

**Date:** 2026-05-09
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że rules injection mechanism (architecture: `_maybe_inject_rules`
→ `_inject_pending` → `force_idle` → `_do_inject_rules` → feed_child VTE)
działa end-to-end. Rules text MUST be visible w session log po inject.

## Pre-state setup

1. Project dir `/tmp/ctx175-rules` (empty)
2. `ctx init bterminal_test_175 "Rules injection E2E #175" /tmp/ctx175-rules`
   → registers project + work_dir mapping
3. `ctx rules add bterminal_test_175 "TEST_RULE_175_CANARY: rules injection E2E marker"`
4. **`ctx rules config bterminal_test_175 --inject-every 5 --refresh-every 10`**
   (default 20/50, lowered for test efficiency)
5. `CLAUDE.md` written + AGENTS.md/AIDER.md symlinks (per #174)
6. Add session via REST + spawn Claude tab (idx=3)

## Critical bug found w pre-test diagnostic

`ctx rules config` **shows defaults** (`inject_every=20 (default)`)
**WITHOUT inserting row** into `rules_config` table. `_maybe_inject_rules`
queries DB → row=None → falls back to hardcoded `inject_every=100`.

Workaround: explicit `ctx rules config <project> --inject-every 5`
**INSERTS** row → DB query succeeds → desired threshold used.

This is a **subtle pitfall** for production users: "config show" reads
defaults but doesn't persist. User needs to explicitly `--set` to
materialize.

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | Pre-state: ctx project + rules + CLAUDE.md created | (CLI probes) | ✓ DB sessions row + rules row + rules_config row |
| 1 | Spawn Claude tab idx=3 | `01-claude-tab-spawned` | ✓ BT main z 3 sessions in sidebar |
| 2 | Simulate 25 + 5 prompts (35 total). At 30 prompts (cross 5×6 boundary) | `02-inject-pending-state` | ✓ REST simulate_prompt response: `inject_pending: ["bterminal_test_175", 30, 10]` |
| 3 | force_idle → _do_inject_rules fires | (REST output: `had_pending: false` po inject) | force_idle wykonał inject + cleared pending |
| 4 | Final state — rules + ctx refresh visible w VTE + Claude response | `03-after-force-idle` | ✓ kluczowy screenshot |

## Final state evidence (screenshot 03)

VTE pokazuje (od góry):
1. **Rules block:** "PRZYPOMNIENIE REGUŁ [bterminal_test_175] (co 20 promptów)" (UI shows default 20 from BT — actual fired at count=30 = 5×6 mod inject_every=5)
2. **Canary rule:** "• **TEST_RULE_175_CANARY: rules injection E2E marker**"
3. **Claude response:** "I'm Claude Opus 4.7 (1M context). **The rules injection canary is visible: TEST_RULE_175_CANARY: rules injection E2E marker**. What would you like to work on?"
4. **ctx refresh** (since count=30 % refresh_every=10 == 0):
   ```
   === project context refresh [bterminal_test_175] ===
   PROJECT: bterminal_test_175 — Rules injection E2E #175
   DIR: /tmp/ctx175-rules
   ```
5. Stats bar: opus-4-7 model, $0.0014 cost, ↑9.1K ↓70 tokens, 64% plan usage

**Claude faktycznie odpowiedział powołując się na canary rule** — to
ostateczne potwierdzenie że rules injection dotarł do model context.

## Inject log (`/tmp/bterminal_inject.log`)

```
2026-05-09 09:23:11.920695: pending set project=bterminal_test_175 count=30
2026-05-09 09:23:14.182174: injecting 266 chars (rules) for bterminal_test_175
2026-05-09 09:23:15.092452: injecting 446 chars (ctx refresh) for bterminal_test_175
```

**3 chronological events:** pending set → rules injection → ctx refresh.

## Feed log decoded (REST `/api/debug/feed_log?label=rules_inject`)

```
════════════════════════════════════════════════════
PRZYPOMNIENIE REGUŁ [bterminal_test_175] (co 5 promptów)
════════════════════════════════════════════════════
• TEST_RULE_175_CANARY: rules injection E2E marker
════════════════════════════════════════════════════
```

This is the **exact byte stream** sent from BT → VTE → Claude.

## Per spec acceptance

- [x] BT installed pre-test ✓
- [x] Project z rules block w CLAUDE.md ✓
- [x] Spawn Claude tab ✓
- [x] Prompt 100x (cross threshold) — symulowane 30 prompts (cross inject_every=5)
- [x] **Screenshot inject_pending state** — `02-inject-pending-state` + REST output `inject_pending: ["bterminal_test_175", 30, 10]` ✓
- [x] force_idle ✓
- [x] **Screenshot session log po inject (rules text WIDOCZNIE w log)** — `03-after-force-idle` pokazuje:
   - rules block z canary rule
   - ctx refresh
   - Claude response acknowledging canary

## Acceptance checklist

- [x] 3 screenshoty zachowane
- [x] Każdy Read-tool reviewed
- [x] Pre-state: ctx project + rules + CLAUDE.md
- [x] inject_pending set po cross threshold (count=30)
- [x] force_idle triggered _do_inject_rules
- [x] Rules text w VTE (canary visible)
- [x] Claude responded with canary echo
- [x] ctx refresh także fired (refresh_every=10, count=30 % 10 == 0)
- [x] Inject log + feed_log zachowane jako evidence
- [x] Methodology #164 spełniona

## Bug observation (non-blocking)

**`ctx rules config` defaults visibility != persistence:**
- `ctx rules config <project>` (no flags) shows "inject_every=20 (default)"
- BUT NIE inserts row into rules_config table
- `_maybe_inject_rules` reads DB → row missing → falls back to `inject_every=100` (hardcoded)
- User confused: "config says 20 but injection nigdy nie fires?"

**Fix idea:** ctx CLI should auto-insert default row at first `ctx rules add`
or `ctx init` so subsequent reads are consistent.

## Verdict

**PASS** — Rules injection mechanism works end-to-end:
- inject_pending set at correct count (multiple of inject_every)
- force_idle triggers _do_inject_rules
- Rules + ctx refresh fed to VTE via feed_child
- Claude actually receives + parses + responds with canary
- Inject log + feed_log captured as audit trail

Methodology #164 spełniona: 3 screenshotów + Read-tool + REST probes
+ DB inspection + decoded byte stream + bug observation.

**Bug found w setup phase:** `ctx rules config` defaults != persisted.
Documented for future testers.
