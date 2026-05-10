# Release QA Process & Methodology (TESTER ROLE)

**Dokument kanoniczny.** Każdy pre-release manual QA wykonywany jest
według tej metodologii. Tester (Claude lub człowiek) wykonuje wszystkie
sub-taski z #165-#179 i dla KAŻDEGO musi mieć wizualny dowód.

---

## NON-NEGOTIABLE rules

1. **KAŻDY krok testu musi zostać udokumentowany screenshotem.** Bez
   screenshota — krok NIE JEST zaliczony.
2. **NIE wolno uruchamiać czegoś "w tle" z założeniem że działa.** Jeśli
   odpalasz `install.sh` — śledzisz output na bieżąco. Jeśli klikasz w
   GUI — screenshot każdego kroku.
3. **NIE wolno robić kilkuminutowych pauz "aż się załaduje".** Jeśli
   coś trwa — co 5-10s sprawdzasz stan (gnome-screenshot + scp pull +
   Read tool).
4. **KAŻDY screenshot musi być przejrzany (Read tool) przez testera
   PRZED zaliczeniem kroku.** "Spawned BT" bez screenshot głównego okna
   ≠ działa.
5. **Akceptacja każdego sub-task wymaga:**
   - (a) wszystkie screenshoty zachowane w `smoke-logs/release-qa/<task-id>/`
   - (b) checklist evidence wypełniony
   - (c) NIE wolno `tasks done` bez wszystkich evidence checked
6. **Negative tests:** gdy test wymaga "nie pokazuje błędu X" —
   screenshot MUSI nie zawierać X. Read tool ogląda obraz, nie polega
   na `grep stdout`.
7. **WSZYSTKIE TESTY WYKONUJESZ NA VM (vm-test).** NIGDY na hoście.
   - Host służy tylko do edytowania kodu + sterowania VM przez ssh
   - `xdotool`/`gnome-screenshot` zawsze przez `ssh vm-test "DISPLAY=:0 …"`
   - Test który "działa lokalnie ale nie był na VM" = nie zaliczony
   - Wyjątkiem są PIN tests (pure pytest na host) które weryfikują
     **strukturę** test scriptów; samo wykonanie scenariusza ZAWSZE VM
   - Sync zmian na VM przed testem: `scp <files> vm-test:/home/michal/BTerminal/`
     lub pełny rebuild: `tools/vm_sync.sh`
   - Stan VM przed testem zawsze potwierdzony przez `ssh vm-test "<probe>"`

---

## Struktura każdego sub-testu

Każdy sub-task musi mieć sekcje:

| Sekcja | Opis |
|--------|------|
| **Cel** | jedno-zdaniowy goal |
| **Pre-state** | stan VM przed (BT installed/not, configs present, etc.) |
| **Kroki** | numerowana lista xdotool/manual actions |
| **Evidence** | lista screenshot files które MUSZĄ powstać + co na nich |
| **Acceptance** | bool checklist, każda pozycja musi być TRUE |
| **Post-state** | stan VM po teście (dla następnego sub-testu) |

---

## Framework techniczny

### Tools używane

| Tool | Purpose |
|------|---------|
| `xdotool key/click` | symulacja interakcji user |
| `xdotool search --name X windowactivate --sync` | precyzyjny focus |
| `gnome-screenshot -f /tmp/X.png` | capture VM screen |
| `ssh vm 'cat /tmp/X.png' > local/X.png` | pull dla Read tool |
| Read tool | **wizualnie** ocenia screenshot (Claude WIDZI obraz) |
| `tools/_e2e_live_monitor.sh` (#156) | streaming klatek co 2s podczas dłuższych ops |

### Helpers cumulative (z E2E #157-#163)

- `_xfocus_bt` — precyzyjny focus na "BTerminal — Terminal" (omija
  gnome-terminal cwd matches)
- F10 menubar nav (Alt+F idzie do VTE bash readline; F10 wymagany)
- chained xdotool key (Down Return jako single call — nie osobne ssh)
- Auto-highlight 1st item po Return na menubar root → Down counts to N-1
- `?force=true` przy AI tab close (tabs mają active task)
- REST endpoints (`/api/sessions/*`, `/api/window/state`,
  `/api/tabs/ai/<prov>`, `/api/sidebar/context_menu/<id>`)
- `_dismiss_dialog`: Esc + Return (alt+F4 BANNED — zamyka BT main window)
- Locale-agnostic error matchers (PL "Brak dostępu" vs EN "Permission
  denied"; prefer markers like `BTERMINAL_FRESH_INSTALL_FAILED`)

### Common pitfalls (z bugów znalezionych w #157-#163)

| Pitfall | Reason | Workaround |
|---------|--------|------------|
| `alt+f` w VTE | bash readline forward-word | użyj F10 |
| `xdotool key Down; xdotool key Return` | osobne ssh hopy gubią menu focus | chain `xdotool key --delay 100 Down Return` |
| `xdotool search "BTerminal"` | matches gnome-terminal cwd=~/BTerminal | precyzyjny pattern `BTerminal — Terminal` |
| `alt+F4` for dismiss | zamyka BT main window | użyj Escape + Return |
| URL `?action=X&provider=Y` w bash | `&` to background op | single-quoted ssh body |
| `grep -c file1 file2` | per-file count z prefix | `cat file1 file2 \| grep -c` |
| `\|\| echo 0` fallback | dorzuca drugi 0 do count | `; true` zamiast |
| Real install.sh w setup | npm install 1+ min | fast `mkdir/touch/ln -sf` layout |
| BT spawn bez DISPLAY | Gtk init blokuje | `sleep 999999` jako fake process |
| `chmod -R a-w INSTALL_DIR` | rm-rf nadal usuwa (parent w-) | `chmod a-w PARENT` |

---

## Hard requirements za każdy release

### Instalator
- ✓ Działa w każdym kierunku (fresh / update / re-run)
- ✓ Przerwana instalacja CZYŚCI nieudane operacje (rollback OK)
- ✓ Aktualizator ZAWSZE działa (Tools → Check for updates)
- ✓ `install.log` pokazuje pełny audit trail każdego runa
- ✓ `flock` blokuje parallel install (BTERMINAL_INSTALL_LOCKED marker)

### AI Providers (Claude / Copilot / Aider)
- ✓ Czysta sesja KAŻDEGO CLI uruchomiona przez UI (NIE manual terminal)
- ✓ Wpisany prosty prompt (np. "what is 2+2?") przez UI input
- ✓ Screenshot odpowiedzi pokazany — Claude/Copilot/Aider WIDOCZNIE
  odpowiada
- ✓ NIE "not found" / NIE error dialog
- ✓ Context file (`CLAUDE.md` / `AGENTS.md` / `AIDER.md`) auto-created
  w `project_dir`
- ✓ Rules injection — verify rules text faktycznie pojawia się w
  session log po prompt

### UI
- ✓ Menu (File / View / Tools) wszystkie items klikalne, dialogi się
  otwierają
- ✓ Sidebar Add/Edit/Delete/Run-as flow działa
- ✓ Options dialog mieści się w 80% workarea (#152), collapse cycle
  zachowuje state (#153)
- ✓ Theme + language live switch (Options → Save → applied bez restart)

---

## Evidence folder structure

```
smoke-logs/release-qa/
├── task-165-install-fresh/
│   ├── screenshots/
│   │   ├── 100501-pre-state.png
│   │   ├── 100545-wizard-welcome.png
│   │   ├── 100612-license-accepted.png
│   │   ├── ...
│   │   └── 101003-summary-success.png
│   ├── install.log              # copy z VM
│   ├── install_errors.json      # copy z VM
│   └── checklist.md             # manual evidence audit
├── task-166-update/
│   └── ...
└── task-NNN-...
    └── ...
```

Naming convention dla screenshotów: `<HHMMSS>-<step-description>.png`.
Czas chronologiczny → łatwo prześledzić sequence.

---

## Sub-tasks (#165-#179)

| # | Task | Acceptance | Required screenshots |
|---|------|-----------|---------------------|
| #165 | Install fresh from empty VM | `bterminal --version` works post-install | wizard pages 1-5, summary success |
| #166 | Update flow (older → current) | VERSION = HEAD, no errors in install_errors.json | "Update available" dialog, progress, restart prompt |
| #167 | Uninstall (no --purge) | BT removed, `~/.config/bterminal` preserved | wizard summary "Uninstall finished" |
| #168 | Uninstall --purge | `~/.config/bterminal` + `~/.claude-context` removed | wizard summary, ls evidence |
| #169 | Fix flow (5 break scenarios from #143) | `detect_install_state == "installed"` post-fix | per-scenario pre/post |
| #170 | Interrupt mid-install + cleanup | ZERO partial state (no BT files, no AI CLIs, no stale lockfile) | rollback message, ls residue |
| #171 | Claude session: spawn → prompt → response | Claude visibly responds to prompt | tab spawn, prompt typed, response visible |
| #172 | Copilot session: spawn → prompt → response | Copilot visibly responds | per #171 |
| #173 | Aider session: spawn → prompt → response | Aider responds (requires ollama running) | banner + qwen load + response |
| #174 | Context file creation | CLAUDE.md/AGENTS.md/AIDER.md auto-created in project_dir | ls before/after per provider |
| #175 | Rules injection verify | Rules text appears in session log after threshold | inject_pending state, force_idle, post-inject log |
| #176 | Tools menu items | Updates/Errata/Diagnostics/Install-deps each opens dialog | per dialog open |
| #177 | Sidebar CRUD click flow | Add/Edit/Delete/Run-as dialog states verified | per CRUD step |
| #178 | Options dialog full expansion | All sections expanded + saved + collapsed | baseline, expanded, scroll, collapse |
| #179 | Theme + language live switch | Light theme applied + Polski menu after Save | before/after each switch |

---

## Checklist template (per sub-task)

Każdy sub-task ma własny `checklist.md` w swojej evidence folder. Format:

```markdown
# Task #NNN — <name>

**Date:** YYYY-MM-DD
**Tester:** Claude / <name>
**VM:** vm-test
**Pre-state:** <verified state before test>

---

## Cel
<one sentence>

## Kroki

| # | Action | xdotool / shell | Screenshot evidence | Visual review |
|---|--------|-----------------|---------------------|---------------|
| 1 | ... | ... | `screenshots/HHMMSS-N.png` | ✓ Read by tester |
| 2 | ... | ... | `screenshots/HHMMSS-N.png` | ✓ Read by tester |
| ... |

## Acceptance checklist

- [ ] Wszystkie screenshoty istnieją i są niepuste (>1KB)
- [ ] Każdy screenshot przejrzany (Read tool) przez testera
- [ ] Pre-state matched (VM was in expected state)
- [ ] Post-state matches expected (artifacts created/removed)
- [ ] No FATAL/Traceback markers w install.log/bt-e2e.log
- [ ] Test acceptance criteria met (per task spec)

## Verdict

PASS / FAIL — <one sentence summary>
```

---

## Tester workflow

1. **Setup**: ssh vm-test, ensure VM jest w expected pre-state.
2. **Start live monitor** (#156): `tools/_e2e_live_monitor.sh start`
   → SESSION_DIR. Daje continuous screenshot stream co 2s.
3. **Per krok**: xdotool action → `tools/_e2e_live_monitor.sh tag NAME`
   → `Read smoke-logs/.../tag-NAME.png` aby visually verify.
4. **Per failure**: NIE pomijać. Zapisać screenshot fail + logs +
   stop. Mark task FAIL z opisem.
5. **End**: `tools/_e2e_live_monitor.sh stop`. Copy artifacts do
   `smoke-logs/release-qa/<task-id>/`.
6. **Checklist**: wypełnić `checklist.md`. NIE `tasks done` bez
   wszystkich items checked.

---

## Skrypt sanity check przed release

`tools/release_qa_sanity.sh` (do napisania osobno) sprawdza że:
- Wszystkie sub-tasks (#165-#179) mają evidence folder
- Każda folder zawiera `checklist.md` + `screenshots/` z >0 plików
- Żaden checklist nie ma niezakończonych `[ ]` items
- Combined regression (`./tools/test_all.sh`) zielony

Bez tego sanity check — release JEST zablokowany.

---

## Wersja dokumentu

- **2026-05-08** — initial version (post #156-#163 helpers harvested)
- Update na każde nowe sub-task lub wprowadzony pitfall

---

**Disclaimer:** Ten dokument JEST source-of-truth. Jeśli czegoś tu nie
ma, to nie jest częścią release QA. Aby dodać nowe wymaganie — najpierw
update tego dokumentu, potem dodaj sub-task.
