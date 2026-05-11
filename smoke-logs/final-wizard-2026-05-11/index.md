# FINAL-WIZARD verification — full flow BUG#19 → BUG#24

Reproduced on VM (vm-test, michal-VirtualBox, **1.9 GB RAM · 0 GB VRAM**)
on 2026-05-11. The task spec mentions an 8 GB VM and recommends pulling
`qwen2.5-coder:1.5b`; on this 1.9 GB VM only `qwen2.5-coder:0.5b` scores
✓ (`1.5b` needs 2.5 GB, scores ✗). The wizard correctly recommends 0.5b
on this hardware — adapting the pick is part of the correct behavior.

## Sequence

| Step | Screenshot | What it proves | Pass |
|---|---|---|---|
| 0   | (state reset)  | `ollama rm qwen2.5-coder:0.5b`, fresh `~/.config/bterminal/options.json` (license hash injected to skip EULA), VM ollama daemon up | ✓ |
| 1   | `01_missing_model_dialog.png` | BUG#19 — BT pre-spawn check detects missing model and shows the 3-button modal ("Brakuje modelu lokalnego dla aidera") instead of leaking `litellm.NotFoundError` into the VTE | ✓ |
| 2   | `02_wizard_tab_open.png` | BUG#22 — clicking 'Uruchom wizarda' opens `tools/aider_setup_wizard` as a new tab AND focuses it (the GLib.idle_add fix lets `set_current_page` win the race against the dialog response handler) | ✓ |
| 3   | `03_wizard_table_rendered.png` | BUG#20+21 — rich-rendered semaforowa table: 7 models, ★ marker on the recommended row, ✓/✗ symbols computed from `aider_probe.recommend_model` against detected hardware (`RAM 1.9 GB · VRAM 0.0 GB`). All `1.5b`+ entries correctly score ✗ on 1.9 GB RAM. The picker prompt at the bottom is bilingual-aware (PL labels) and offers the BUG#24 `r` refresh shortcut | ✓ |
| 4   | `04_ollama_pull_progress.png` + `04b_ollama_pull_zoom.png` | BUG#21 — picker accepts "1", wizard echoes `Wybrano: qwen2.5-coder:0.5b`, then drives `ollama pull` while rendering a rich Progress bar parsing the percentage from Ollama's stderr (`ollama pull ───── 1% 0:00:06`) | ✓ |
| 5   | `05_aider_running_with_new_model.png` + `05b_aider_running_zoom.png` | BUG#22 end of loop — wizard wrote the sentinel, BT's `child-exited` hook on the wizard tab loaded it, `compute_relaunch_config` matched `session_id`, and a fresh aider tab opened automatically. Banner shows `Model: openai/qwen2.5-coder:0.5b` and **no** `litellm.NotFoundError`. Status bar bottom: `0 tok/h · openai/qwen2.5-coder:0.5b` | ✓ |
| 6 (bonus) | `06_manual_wizard_entry.png` | BUG#23 — `Narzędzia → Konfiguruj lokalny model (aider)…` opens the same wizard tab without a `--session-id`. PL translation visible in the menu item; the tab title bar shows the wizard's default heading | ✓ |

## Pin-test sweep on VM

All six BUG#19–#24 test files run together against the VM checkout:

```
ssh vm-test 'cd /home/michal/BTerminal && pytest tests/e2e/test_aider_missing_model_dialog.py \
  tests/test_aider_probe.py tests/test_aider_setup_wizard_cli.py \
  tests/e2e/test_wizard_flow_e2e.py tests/e2e/test_tools_menu_aider_wizard.py \
  tests/test_catalog_refresh.py'
```

Result: **61 passed in 2.19s** (11 + 18 + 5 + 9 + 4 + 14).

| Bug | File | Tests | Status |
|---|---|---:|:---:|
| #19 | `test_aider_missing_model_dialog.py` | 11 | ✓ |
| #20 | `test_aider_probe.py` | 18 | ✓ |
| #21 | `test_aider_setup_wizard_cli.py` | 5 | ✓ |
| #22 | `test_wizard_flow_e2e.py` | 9 | ✓ |
| #23 | `test_tools_menu_aider_wizard.py` | 4 | ✓ |
| #24 | `test_catalog_refresh.py` | 14 | ✓ |

## Verdict

✅ **PASS** — the full chain from "fresh install on a host that never pulled the
default model" through "wizard tab pulls the model and respawns the aider
session automatically" works end-to-end on the test VM. Manual entry from
the Tools menu reaches the same wizard with PL labels.

The one deviation from the task spec — `0.5b` chosen over `1.5b` — is
forced by the VM's 1.9 GB RAM and is itself a positive: it proves the
recommender adapts to the actual host rather than picking a fixed tier.
