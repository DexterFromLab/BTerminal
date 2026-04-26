# BTerminal — branch `feat/sidecar-plugins`

Branch roboczy do dodania **drugiego kontraktu pluginów** (sidecar HTTP) obok
istniejącego (in-process GTK). Cel: móc uruchamiać narzędzia z `agent_controller`
i `agent-tester` jako pluginy BTerminala, **bez** ruszania obecnych mechanik
(Ctx, Tasks, Memory zostają jak są) i **bez** zmiany sposobu działania
RemoteControll.

Klon BTerminala: `~/workspace/bterminal-plugins/`
Źródło: `~/workspace/ssh_client/` (origin = GitHub `DexterFromLab/BTerminal`,
upstream-local = lokalna ścieżka)
Branch: `feat/sidecar-plugins` z `master`

---

## Zakres ustalony z użytkownikiem

- **Tak:** dodajemy nowy loader pluginów typu *sidecar* (HTTP/subprocess)
- **Tak:** rejestrujemy jako sidecary narzędzia z `agent_controller`
  (`btmsg`, `explorer`, `mr`, `taskboard` — `ctx` zostaje wbudowany) oraz
  `agent-tester`
- **Nie:** **nie** wprowadzamy sekcji „Tryb pracy" / autonomii z
  `IntroPromptBuilder`. Sidecary tylko *istnieją* i są opisane w intro
  promptcie — bez zmiany stylu pracy Claude
- **Nie:** **nie** dotykamy istniejących wbudowanych paneli `Ctx`, `Tasks`,
  `Memory`, `Skills`, `Files`, `Plugins` (panel zarządzania starymi pluginami)
- **Nie:** **nie** zmieniamy kontraktu GTK pluginów (`BTerminalPlugin`,
  `create_plugin(app)`) — RemoteControll dalej działa identycznie

---

## Mapa kodu (stan zastany)

| Projekt | Lokalizacja | Postać | Rola |
|---|---|---|---|
| **BTerminal** | `~/workspace/ssh_client/bterminal.py` | 1 plik · ~10 300 linii · GTK3+VTE | Produkcyjny terminal SSH/Claude (wersja w użyciu, **ten branch jest jego klonem**) |
| **agent_controller** | `~/workspace/agent_controller/` | Modularny SOLID-rewrite (`src/{core,services,config,ui}`, `plugins/`) | Refaktor z nowymi mechanikami — **źródło sidecarów** |
| **agent-tester** | `~/workspace/agent-tester/` | FastAPI :8081 + MCP stdio + Vue UI | Autonomiczny QA-agent (Playwright + CV) — **dołączamy jako sidecar** |
| **RemoteControll** | `~/workspace/RemoteControll/remote_controll.py` | 1 plik · 3 021 linii · GTK3 plugin | Plugin BTerminala (HDMI grabber + USB HID + REST :7777) — **bez zmian** |

---

## Dwa kontrakty pluginów

### Istniejący — in-process GTK (zostaje)

```
plugin = importlib.spec_from_file_location(...)        # ~/.config/bterminal/plugins/
plugin = module.create_plugin(app)                     # factory
class XPlugin(BTerminalPlugin):
    activate(app) -> Gtk.Widget                        # rysuje panel w sidebar_stack
    deactivate()
    get_keyboard_shortcuts() -> [...]
    get_session_context() -> str                       # do intro promptu
    on_sidebar_shown()
```

Loader: `bterminal.py:9705 _load_plugins`.
Reprezentant: **RemoteControll** (`~/workspace/RemoteControll/remote_controll.py`).

### Nowy — sidecar HTTP (dodajemy)

```
default_config.json: { name, plugin_address, healthcheck_url,
                        run_command, reset_command, prompt }
SidecarDiscovery.discover() -> [ {manifesty} ]
SidecarRunner.start(id, "python -m plugins.X.run")     # subprocess.Popen
HealthChecker -> ping /health                          # liveness
```

Wzorzec: `agent_controller/src/services/{plugin_discovery,plugin_runner,health_checker}.py`.
Reprezentanci: **`btmsg` :8766**, **`explorer` :8770**, **`mr`**, **`taskboard`**,
**`agent-tester` :8081**.

---

## Co przenosimy z agent_controller

Tylko warstwa runtime sidecarów — bez `core/` (intro builder, autonomia,
session controller, rules engine). Ctx/Tasks/Memory zostają wbudowane.

| Moduł | Plik źródłowy | Co daje |
|---|---|---|
| `PluginDiscovery` | `agent_controller/src/services/plugin_discovery.py` | Skan `default_config.json` → lista manifestów |
| `PluginRunner` | `agent_controller/src/services/plugin_runner.py` | `subprocess.Popen` + `stop_all()` |
| `HealthChecker` | `agent_controller/src/services/health_checker.py` | Pingowanie `/health` |
| Cały `plugins/` (5 sidecarów) | `agent_controller/plugins/{btmsg,explorer,mr,taskboard,ctx*}` | Mikroserwisy do działania obok BTerminala (`ctx` z agent_controller pomijamy — BTerminal ma swój wbudowany) |

W BTerminalu zostają w nowych modułach (lub jako klasy w `bterminal.py` —
do decyzji w trakcie):
- `SidecarPluginLoader` — runtime (analog do `_load_plugins`)
- `SidecarPanel` (ewentualnie) — lista sidecarów + start/stop, obok
  istniejącego `PluginManagerPanel`

---

## RemoteControll — co MUSI zostać nietknięte

Stałe punkty zaczepienia obecnego kontraktu (jeśli któryś znika, plugin pęka):

| Wymaganie | Skąd | Dlaczego |
|---|---|---|
| Klasa bazowa `BTerminalPlugin` (definiowana inline w pluginie) | `remote_controll.py:951` | Plugin dziedziczy stąd |
| `create_plugin(app)` factory | `remote_controll.py:3019` | Wywoływany przez loader GTK |
| `app.notebook` (GTK Notebook) | używane w `_get_active_terminal_tab()` | Plugin pobiera aktywną kartę żeby wstrzykiwać tekst |
| Globalny `key-press-event` (Ctrl+Space) | `app.add_accel_group(...)` | Hotkey-capture |
| `get_session_context()` przy starcie sesji Claude | `bterminal.py:2501` | Intro prompt mówi Claude o REST :7777 |
| `~/.config/bterminal/remote_controll.json` | konfiguracja | Wstecznia kompatybilność (XDG) |
| `~/.config/bterminal/remote_controll_skills/` | katalog skills | jw. |
| `_ensure_claude_context` zapisujący CLAUDE.md w katalogu pluginu | `remote_controll.py:2954` | **Bez zmian** w tym branchu (jeśli per-workspace, to oddzielny ticket) |

---

## Agent Tester — sposób uruchamiania

Już ma trzy interfejsy: REST (FastAPI :8081), MCP stdio (`agent.mcp_server`),
Vue UI. Przyjmujemy plan:

1. **Manifest sidecar** w `~/workspace/agent-tester/default_config.json`
   (lub w `~/.config/bterminal/sidecars/agent-tester.json`):
   ```json
   {
     "name": "agent-tester",
     "plugin_address": "http://127.0.0.1:8081",
     "healthcheck_url": "http://127.0.0.1:8081/health",
     "run_command": "uvicorn api.main:app --port 8081",
     "reset_command": "pkill -f 'uvicorn api.main:app' || true",
     "prompt": "<skrót REST/MCP toolset jako curl>"
   }
   ```
2. `SidecarPluginLoader` startuje go razem z innymi sidecarami przy
   inicjalizacji BTerminala
3. MCP zostaje nietknięte — działa równolegle (Claude i tak woła `test_run`,
   `browser_screenshot` etc. przez MCP stdio)

---

## Ryzyka i pułapki

1. **Kolizja portów.** Sidecary używają `:8765/8766/8770/8081/7777`.
   Druga instancja BTerminala → porty zajęte. `SidecarRunner.start` musi
   wykryć przez `HealthChecker.ping` i traktować "już działa" jako sukces
   (idempotent start).
2. **Subprocesy wiszą po crashu BTerminala.** `Popen` bez process group →
   sieroty trzymają porty. Wymóg: `start_new_session=True` +
   `atexit.register(sidecar_runner.stop_all)`.
3. **Kolizja nazw paneli.** Sidecary z `agent_controller` nazywają się
   `ctx`, `taskboard` — nakładają się na wbudowane `Ctx`, `Tasks` BTerminala.
   Decyzja: **ignorujemy `ctx` z agent_controller** (BTerminal ma swój),
   `taskboard` rejestrujemy pod prefiksem `sidecar:taskboard` lub czytelnym
   tytułem ("Taskboard (web)") żeby nie mylić z wbudowanym panelem `Tasks`.
4. **`agent-tester` zakłada `localhost:8081` w `mcp_server.py:25`**, ale ma
   override przez env `AGENT_TESTER_API_URL`. `SidecarRunner` musi go
   ustawiać jeśli zmienimy port w manifeście.
5. **`bterminal.py` to monolit 401 KB.** Refaktor "in-place" jest ryzykowny.
   W tym branchu **nie rozbijamy** monolitu — dokładamy nowe klasy w tym
   samym pliku albo w osobnym module `bterminal_sidecars.py` (do decyzji
   przed pierwszym commitem kodu).

---

## Decyzje przyjęte (runda 2)

| # | Decyzja | Uzasadnienie |
|---|---|---|
| 1 | **Cały kod sidecarów dolepiamy do `bterminal.py`** (nowe klasy obok istniejących). Refaktor pliku to oddzielne zadanie. | Trzymamy konwencję single-file repo |
| 2 | **Manifesty w `~/.config/bterminal/sidecars/*.json`** (jeden plik na sidecar) | Spójność z `~/.config/bterminal/{sessions,plugins,options,plugins.json}`; jawna rejestracja; portowalność (manifest niezależny od ścieżek do repo); łatwo edytować bez ruszania repo źródła |
| 3 | **`agent-tester` startuje on-demand** (nie auto przy starcie BTerminala) | Ciężkie procesy (FastAPI + ewent. Playwright) odpalamy tylko gdy zakładka tego potrzebuje |
| 4 | **Naprawiamy korelację zakładka ↔ plugin** — dwie warstwy: hot enable/disable globalny + per-zakładka opt-in | Dziś toggle wymaga restartu, pluginy są globalne dla wszystkich zakładek — to centralny brak |

### Anatomia braku w korelacji zakładka ↔ plugin (stan dzisiaj)

- `PluginManagerPanel._on_enabled_toggled` (`bterminal.py:8834`) — zapisuje
  `~/.config/bterminal/plugins.json`, wyświetla "Restart BTerminal for changes
  to take effect", **nie** wywołuje `activate/deactivate` w runtime
- Pętla `for plugin in self.app._plugins.values()` (`bterminal.py:2502`) —
  iteruje po wszystkich pluginach załadowanych przy starcie. Brak filtra
  per-tab, brak filtra po `enabled` (jeśli plugin był enabled przy starcie,
  zostaje w dictie nawet po zmianie toggle)
- `TerminalTab` (l. 2330) nie ma żadnego pola typu `enabled_plugins` —
  korelacja sesja↔plugin nie istnieje strukturalnie

Plan naprawy:
- **Hot toggle globalny:** `_on_enabled_toggled` faktycznie wywołuje
  `plugin.activate(self.app)` / `plugin.deactivate()` i ustawia/zdejmuje
  panel sidebar bez restartu. Dotyczy zarówno GTK pluginów (RemoteControll
  — sprawdzić czy `deactivate()` jest reentrant) jak i sidecarów (`SidecarRunner.start/stop`)
- **Per-zakładka opt-in:** `TerminalTab` zyskuje
  `self.enabled_plugins: set[str] | None` (None = "wszystkie globalnie
  włączone"). `ClaudeCodeDialog` przy nowej sesji pokazuje checkbox-listę
  pluginów; kontekst-menu na zakładce: *„Pluginy w tej sesji…"* otwiera tę
  samą listę. Pętla intro-promptu (l. 2502) filtruje po
  `tab.enabled_plugins`
- **Refcount sidecarów:** sidecar startuje gdy pierwsza zakładka go
  używa, zatrzymywany gdy ostatnia odpina (lub się zamyka). Zapobiega
  trzymaniu portów przez zombie procesy gdy nikt z nich nie korzysta

---

## Plan etapów (zaktualizowany — do akceptacji przed kodem)

| Etap | Co | Test akceptacyjny |
|---|---|---|
| **0** | Branch + ten README | (zrobione, commit `015a42f`) |
| **1** | `SidecarDiscovery` + `SidecarRunner` + `HealthChecker` jako nowe klasy w `bterminal.py` (ok. 200 linii). Manifest schema: `{name, plugin_address, healthcheck_url, run_command, reset_command, cwd, prompt}`. Lokalizacja manifestów: `~/.config/bterminal/sidecars/*.json` | Smoke test: ręczne dodanie 1 manifestu, BTerminal startuje, sidecar wykryty, `start()` faktycznie odpala proces, `stop()` go zabija (z `start_new_session=True`), `atexit` ubija wszystko |
| **2** | Wpięcie loadera do `BTerminalApp.__init__` (po `_load_plugins`). **Bez** auto-startu sidecarów — tylko discovery | BTerminal startuje, w logach lista wykrytych sidecarów, RemoteControll i wszystkie wbudowane panele dalej działają |
| **3** | Manifesty dla `btmsg`, `explorer`, `mr`, `taskboard`, `agent-tester`. Skopiowane/przepisane z `agent_controller/plugins/*/default_config.json` z dodanym polem `cwd` (root agent_controllera lub agent-testera) | Wszystkie 5 manifestów wykryte; ręczny `start` jednego (np. `btmsg`) wstaje na :8766, `/health` zwraca ok |
| **4 (kluczowy)** | **Korelacja zakładka ↔ plugin.** (a) Hot toggle globalny w `PluginManagerPanel` — `_on_enabled_toggled` woła activate/deactivate bez restartu. (b) `TerminalTab.enabled_plugins`. (c) `ClaudeCodeDialog` z listą pluginów (GTK + sidecar) z checkboxami. (d) Pętla intro-promptu (l. 2502) filtruje po `tab.enabled_plugins`. (e) Refcount: sidecar startuje przy pierwszej zakładce, kończy się przy ostatniej | Dwie zakładki Claude jednocześnie: jedna z `btmsg` zaznaczonym, druga bez. `btmsg` :8766 wstał gdy ruszyła pierwsza zakładka. Toggle "off" w PluginManager — plugin natychmiast znika z sidebara, sidecar gaśnie. Zamknięcie zakładki używającej `btmsg` (i to jedynej) → `btmsg` :8766 gaśnie automatycznie. RemoteControll: hot disable / hot enable bez crashu BTerminala |
| **5** | `prompt` z manifestu sidecara doklejany do intro promptu Claude (analog `get_session_context()` dla GTK). Filtrowany per-tab przez `enabled_plugins` (z Etapu 4) | Intro prompt nowej sesji Claude zawiera sekcje tylko aktywnych w niej sidecarów; RemoteControll dalej widoczny |
| **6** | (opcjonalne, później) Drobny `SidecarStatusPanel` w sidebarze — lista sidecarów, status `/health`, przycisk Restart | Można restartować pojedynczy sidecar bez restartu BTerminala |

Etap 6 jest opcjonalny i może wypaść z tego brancha. Etapy 1–5 są w zakresie.

---

## Konwencja manifestu sidecara

Plik `~/.config/bterminal/sidecars/<name>.json`:

```json
{
  "name": "btmsg",
  "title": "BtMsg (web)",
  "description": "Inter-agent messaging — send/receive messages between Claude Code sessions.",
  "plugin_address": "http://127.0.0.1:8766/api",
  "plugin_dashboard": "http://127.0.0.1:8766/",
  "healthcheck_url": "http://127.0.0.1:8766/api/health",
  "run_command": "python3 -m plugins.btmsg.run",
  "reset_command": "pkill -f 'plugins.btmsg.run' || true",
  "cwd": "/home/bartek/workspace/agent_controller",
  "env": { "BTMSG_PORT": "8766" },
  "auto_start": false,
  "prompt": "## btmsg — Inter-Agent Messenger\n\nBase URL: http://127.0.0.1:8766/api\n..."
}
```

Pola opcjonalne: `title` (domyślnie `name`), `env`, `auto_start` (default
`false` — sidecar startuje on-demand z zakładki), `cwd` (jeśli `run_command`
używa `python -m plugins.X.run`, MUSI wskazywać root projektu).

---

## Co zostaje DOKŁADNIE jak teraz

- `bterminal.py` — wszystkie istniejące klasy bez zmian funkcjonalnych
- Wbudowane panele: `Sessions`, `Ctx`, `Consult`, `Tasks`, `Memory`,
  `Skills`, `Files`, `Plugins`
- Loader GTK pluginów (`_load_plugins`, `_register_plugin`)
- Kontrakt `BTerminalPlugin`, `create_plugin(app)`,
  `get_session_context()`
- `_build_intro_prompt()` — bez sekcji autonomii
- RemoteControll — całkowicie nietknięty
- Konfigi w `~/.config/bterminal/` (sessions, plugins, options)

---

## Pytanie otwarte przed Etapem 1

**Per-zakładka opt-in domyślnie ON czy OFF?**

Gdy user otwiera nową zakładkę Claude, lista pluginów w `ClaudeCodeDialog`:
- **Wariant A:** wszystkie pluginy/sidecary domyślnie zaznaczone
  (opt-out — żeby nie zmieniać dotychczasowego doświadczenia użytkownika
  z RemoteControll, który dziś działa wszędzie)
- **Wariant B:** wszystkie domyślnie odznaczone (opt-in — user świadomie
  wybiera co potrzebuje, mniej zombie sidecarów)
- **Wariant C:** per-plugin domyślna wartość w manifeście
  (`"default_in_session": true|false`); RemoteControll = `true`,
  ciężkie sidecary jak `agent-tester` = `false`

Domyślnie skłaniam się do **C** — daje kontrolę bez ukrytych regresji
i pasuje do `auto_start` z manifestu.

Po decyzji ruszam Etap 1.
