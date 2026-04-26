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

## Plan etapów (do akceptacji przed kodem)

| Etap | Co | Test akceptacyjny |
|---|---|---|
| **0** | Branch + ten README | (zrobione) |
| **1** | `SidecarDiscovery` + `SidecarRunner` + `HealthChecker` jako nowy moduł | Unit testy na `subprocess.Popen` (mockowanym) i `httpx` ping |
| **2** | Wpięcie loadera do `BTerminalApp.__init__` (po `_load_plugins`) | BTerminal startuje, loguje wykryte sidecary, RemoteControll dalej działa |
| **3** | Manifesty dla `btmsg`, `explorer`, `mr`, `taskboard`, `agent-tester` w `~/.config/bterminal/sidecars/` | Wszystkie sidecary startują, `/health` zwraca ok |
| **4** | `get_session_context()` analog dla sidecarów — ich `prompt` z manifestu doklejany do intro Claude | Nowa sesja Claude ma sekcje per-sidecar; RemoteControll dalej widoczny |
| **5** | (opcjonalne) Mały `SidecarPanel` w sidebarze — lista, status `/health`, przycisk Restart | Można zrestartować pojedynczy sidecar bez restartu BTerminala |

Etap 5 jest opcjonalny — jeśli wystarczą sidecary uruchamiane w tle, panel
nie jest konieczny w pierwszej iteracji.

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

## Następny krok

Czekam na decyzję:
1. **Gdzie umieścić nowy kod sidecarów?** Osobny moduł
   `bterminal_sidecars.py` obok `bterminal.py`, czy dokleić klasy do samego
   `bterminal.py` (zachowanie "single file")?
2. **Manifesty sidecarów** — czytamy z `~/workspace/<projekt>/default_config.json`
   (auto-discovery po katalogach) czy z dedykowanego katalogu
   `~/.config/bterminal/sidecars/*.json` (rejestracja jawna)?
3. **Czy w tym branchu `agent-tester` ma się startować automatycznie**, czy
   tylko być wykrywany i startowany ręcznie z panelu?

Po decyzji ruszam Etap 1.
