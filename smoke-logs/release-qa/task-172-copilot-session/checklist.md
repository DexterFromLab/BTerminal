# Task #100 (#172) — Copilot session: spawn → prompt → response

**Date:** 2026-05-08
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że BTerminal spawnuje real Copilot CLI session, prompt
"what is 2+2" daje rzeczywistą odpowiedź. Plus verify `/login`
device-flow prompt screenshot if first run.

## Pre-state (verified)

- [x] BT v1.3.0 running (REST: version=1.3.0)
- [x] `~/.local/bin/copilot --version` → `GitHub Copilot CLI 1.0.43.`
      (BT spawnuje 1.0.44 — auto-update z npm na fresh spawn)
- [x] Saved AI sessions: `E2E_Claude_171` (✦) + `E2E_Copilot_172` (🤖)
- [x] Copilot **already authenticated** (z poprzednich sesji — no `/login` required)

## /login device-flow status

**SKIP — already authenticated.** Copilot pokazał banner z workspace
trust dialog, NOT login prompt. Pierwsza sesja Copilot na VM (przed
testami) zaakceptowała device-flow gdzieś wcześniej. Z `~/.config/
github-copilot/` cache token jest persistent across BT runs.

Per spec "if pierwszy run" — to **NIE jest** pierwszy run, więc
/login screen nie wystąpił. Acceptable.

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | BT main, sidebar z 2 sesjami | `02-bt-running` | ✓ "BTerminal — Terminal [DEBUG-REST :7780]", sidebar pokazuje **E2E_Claude_171** (✦) + **E2E_Copilot_172** (🤖) |
| 1 | Spawn Copilot tab via REST | `03-copilot-tab-spawned` | ✓ Tab "**E2E_Copilot_172** 🤖" otwarty, **GitHub Copilot v1.0.44** banner + "Copilot uses AI. Check for mistakes." + "Confirm folder trust" prompt z `/tmp/e2e-copilot-172` + 3 options (1.Yes, 2.Yes+remember, 3.No) |
| 2 | Send `\r` Enter — accept trust | `04-after-trust-accept` | trust accepted, intro_prompt loaded (bterminal rules visible) + "Environment loaded: 1.0.43_..." |
| 3 | Wait for ready | `05-copilot-intro-loaded` | ✓ "**I'm powered by GPT-5 mini (model ID: gpt-5-mini)**" + "Ready to help with project e2e-copilot-172 — what would you like me to do?" + sample tasks list |
| 4 | Type "what is 2+2" + Enter | `06-copilot-response` | ✓ "**▷ what is 2+2**" + "**▷ 2 + 2 = 4.**" — Copilot **FAKTYCZNIE ODPOWIEDZIAŁ** |
| 5 | Final state | `07-final-copilot-answered` | ✓ stable response, statys bar updated |

## Real Copilot response captured

```
▷ what is 2+2
▷ 2 + 2 = 4.
Thinking (Esc to cancel)...
```

(Copilot kontynuuje "Thinking" po response — natural Copilot UI behavior,
może rozważa kolejne komentarze. Response już dany.)

**Acceptance: Copilot visibly answered the prompt.** NO "command not found",
NO "/login required" error, NO timeout.

## Per spec acceptance

- [x] Add ▼ → Copilot session — visible in sidebar (🤖 icon) ✓
- [x] Save → sidebar entry — `E2E_Copilot_172` z 🤖 icon ✓
- [x] Spawn — Tab opens with Copilot CLI banner ("GitHub Copilot v1.0.44") ✓
- [x] Type 'what is 2+2' — prompt visible ✓
- [x] Enter — Copilot processed ✓
- [x] **Screenshot odpowiedzi (Copilot FAKTYCZNIE odpowiada)** — `06-copilot-response.png`: "▷ 2 + 2 = 4." ✓
- [x] **NIE error / NIE 'not found' / NIE 'login required'** ✓
- [⚠] /login device-flow — **NOT shown** (Copilot already authenticated z poprzednich sesji); acceptable per spec ("jeśli pierwszy run")

## Acceptance checklist

- [x] 4+ screenshots zachowane (mam 7)
- [x] Każdy screenshot Read-tool reviewed
- [x] BT installed pre-test
- [x] Copilot binary OK (~/.local/bin/copilot --version → 1.0.43)
- [x] Sidebar entry created (visual 🤖 icon)
- [x] Copilot tab spawned z Copilot v1.0.44 banner
- [x] Workspace trust prompt accepted (visual progression)
- [x] Intro prompt loaded (bterminal rules + GPT-5 mini model identification)
- [x] User prompt "what is 2+2" sent
- [x] Copilot responded "2 + 2 = 4." (visual ✓)

## Differences vs Claude (#171) flow

| Aspect | Claude (#171) | Copilot (#172) |
|--------|--------------|----------------|
| Trust prompt | "Yes, I trust this folder / No, exit" (2 opt) | "Yes / Yes+remember / No (Esc)" (3 opt) |
| Trust accept | Required `\r` (CR) — `\n` ignored | Single `\r` accepted immediately |
| Banner | "Accessing workspace: ..." + safety check | "GitHub Copilot v1.0.44" + "Copilot uses AI..." |
| Ready signal | "Welcome ... How can I help" | "I'm powered by GPT-5 mini ... Ready to help" |
| Response format | "▷ 2+2 is 4." | "▷ 2 + 2 = 4." |
| Auth | API key (anthropic) | OAuth device-flow (github-copilot, cached) |

## Verdict

**PASS** — Copilot session spawn + prompt + response flow działa
end-to-end. Real GitHub Copilot CLI 1.0.44 spawned w BTerminal tab,
authenticated via cached OAuth token (no /login screen — past
auth), responded to "what is 2+2" with "2 + 2 = 4."

Methodology #164 spełniona: 7 screenshotów + Read-tool review +
real LLM response captured + cross-provider comparison documented.

**Helper pattern z #171 reused successfully** — 5 minut total
including spawn, trust, intro_load, prompt, response. Faster niż
poprzedni task bo intro_prompt protocol the same.
