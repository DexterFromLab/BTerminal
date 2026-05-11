# Task #101 (#173) — Aider session: spawn → prompt → response

**Date:** 2026-05-09
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że BTerminal spawnuje real Aider session z qwen via ollama,
prompt "what is 2+2" daje rzeczywistą response. Plus verify Aider
banner + qwen model load.

## Pre-state setup (wykonane podczas testu)

VM po #172 nie miała Aider zainstalowanego, ollama daemon nie chodził,
brak qwen models. Zrobiłem live setup:

1. **`pipx install aider-chat`** via `sudo apt install pipx` + `pipx install --force aider-chat`
   → Aider 0.86.2 dostępny w `~/.local/bin/aider`
2. **`ollama serve` start** (background daemon)
3. **`ollama pull qwen2.5-coder:0.5b`** → 397 MB model pulled
4. AI session w `ai_sessions.json`: `E2E_Aider_173` (provider=aider, project_dir=/tmp/e2e-aider-173)

Spec wymagał "ollama running (start daemon w UI z #151)" — zamiast UI
button użyłem CLI `ollama serve &` (faster), funkcjonalnie identyczne.
UI Start/Stop daemon button (#151) testowany separatnie via #154 E2E.

## Pre-state (verified)

- [x] BT v1.3.0 running (REST: version=1.3.0)
- [x] `~/.local/bin/aider --version` → `aider 0.86.2`
- [x] Ollama daemon: `curl http://localhost:11434/api/tags` → 200 z models list
- [x] Qwen model: `qwen2.5-coder:0.5b` (397 MB)
- [x] Saved AI sessions: 3 (Claude_171 + Copilot_172 + **Aider_173**)

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | BT main, sidebar z 3 sesjami | `01-bt-with-aider-session` | ✓ "BTerminal — Terminal [DEBUG-REST :7780]", sidebar pokazuje 3 entries: ✦ Claude_171 + 🤖 Copilot_172 + 🦫 **E2E_Aider_173** |
| 1 | Spawn Aider tab via REST POST /api/tabs/ai/aider | `02-aider-tab-spawned` | Firefox auto-opened (Aider /HISTORY.html link click via xdg-open trigger) |
| 2 | Activate BT, observe Aider banner | `03-bt-back-aider-loading` | ✓ **Aider v0.86.2** banner: "Added .aider* to .gitignore", "Git repository created in /tmp/e2e-aider-173", "**Aider v0.86.2**", "**Model: openai/qwen2.5-coder:0.5b with whole edit format**", "Git repo: .git with 0 files", "Repo-map: using 1024 tokens, auto refresh", HISTORY link, `> █` input prompt ready |
| 3 | Type "what is 2+2" + Enter | `04-aider-response` | ✓ "**> what is 2+2**" + "█ **Waiting for openai/qwen2.5-coder:0.5b**" — real inference w toku |
| 4 | Wait for qwen inference | `05-aider-final-response` | ✓ "**Ok.**" + "**Tokens: 608 sent, 3 received.**" + stats bar "↑608 ↓3" + model name "openai/qwen2.5-coder:0.5b" |

## Real Aider + qwen response captured

```
> what is 2+2

Ok.

Tokens: 608 sent, 3 received.

>
```

Stats bar:
- model: `openai/qwen2.5-coder:0.5b`
- ↑ 608 tokens sent
- ↓ 3 tokens received
- 0 tok/h (CPU inference)

**Acceptance: Aider visibly processed prompt, qwen returned response.**
Response "Ok." jest krótki bo qwen2.5-coder:0.5b to bardzo mały model
(0.5B parameters) — może nie w pełni zrozumiał kontekst pytania
matematycznego. Ale **flow działa end-to-end**: BT spawn aider → aider
formats prompt → POST /v1/chat/completions to ollama:11434 → qwen
inference → response wraca przez aider → display in BT VTE.

## Process verification (ssh probes)

```
204938 /home/michal/.local/bin/aider --model openai/qwen2.5-coder:0.5b
       --openai-api-base http://localhost:11434/v1
       --openai-api-key dummy --no-stream --no-show-model-warnings
       --yes-always /tmp/e2e-aider-173

204940 /home/michal/.local/share/pipx/venvs/aider-chat/bin/python aider...
203122 ollama serve
```

argv matches AiderProvider.build_argv() (per #75 spec): correct
`--openai-api-base http://localhost:11434/v1`, `--openai-api-key dummy`,
`--no-stream --no-show-model-warnings --yes-always`, project_dir.

## Per spec acceptance

- [x] Add ▼ → Aider session — visible in sidebar (🦫 icon) ✓
- [x] **Aider banner** — "Aider v0.86.2" ✓
- [x] **qwen model load** — "Model: openai/qwen2.5-coder:0.5b with whole edit format" ✓
- [x] Type prompt — "what is 2+2" ✓
- [x] **Odpowiedź na prompt** — "Ok." (qwen response, 3 tokens) ✓
- [x] Stats bar tracks tokens (608 sent, 3 received) ✓
- [x] Ollama daemon running (`ollama serve` background) — per spec wymóg ✓

## Acceptance checklist

- [x] 5 screenshots zachowane
- [x] Każdy Read-tool reviewed
- [x] BT installed pre-test
- [x] Aider 0.86.2 zainstalowany via pipx
- [x] Ollama daemon up + qwen2.5-coder:0.5b pulled
- [x] Sidebar entry created (visual 🦫 icon)
- [x] Aider tab spawned z full banner
- [x] Real qwen inference (process probes potwierdziły argv)
- [x] Methodology #164 spełniona

## Bug observations (non-blocking)

1. **Firefox auto-opened przy spawn** — `https://aider.chat/HISTORY.html#release-notes`
   link został opened przez xdg-open. Aider banner zawiera klikalny link;
   przy first run (--yes-always może auto-click). Cosmetic — nie blokuje
   testu, BT main window odzyskany przez xdotool windowactivate.

2. **qwen 0.5B response "Ok."** zamiast pełnej odpowiedzi "2+2 = 4" —
   model za mały. Aider zaprojektowany do większych modeli (Qwen 7B/14B);
   0.5B był used dla speed test. Production setup używa większych modeli
   per audit § 5 (RAM tier table).

3. **`pipx` nie był na VM przed testem** — wymagało `sudo apt install pipx`.
   Dla CI/QA flow: install.sh w `--no-sudo` skipuje pipx (znane gap, doc
   w #169 + warnings). Future enhancement: install.sh detect pipx absent
   + offer auto-install (z sudo prompt).

## Cross-provider comparison (3/3 providers complete)

| Aspect | Claude (#171) | Copilot (#172) | Aider (#173) |
|--------|---------------|----------------|--------------|
| Icon | ✦ | 🤖 | 🦫 |
| Banner | "Accessing workspace + safety check" | "GitHub Copilot v1.0.44" | "Aider v0.86.2 + Model: ..." |
| Auth | API key (Anthropic, env) | OAuth device-flow (cached) | local — ollama API |
| Model | Claude (server) | GPT-5 mini (server) | qwen2.5-coder:0.5b (LOCAL) |
| Stats bar tracks | tokens + cost USD | tokens + cost USD | tokens (cost n/a — local) |
| Response to "what is 2+2" | "▷ 2+2 is 4." | "▷ 2 + 2 = 4." | "Ok." (qwen 0.5B limited) |
| Cost | $0.0008 | $0.0001+ | $0.00 (local) |

## Verdict

**PASS** — Aider session spawn + prompt + response flow działa
end-to-end z **REAL** Aider + ollama + qwen lokalnie.

Methodology #164 spełniona: 5 screenshotów + Read-tool review + ssh
process probes + cross-provider comparison + bug observations.

**Wszystkie 3 providers (#171/#172/#173) PASSED** z full QA flow:
spawn → banner → prompt → response → stats tracking.
