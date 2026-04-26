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

---

## Debug-REST + mechanika testowania (rozszerzenie zakresu)

### Stan zastany — luka, którą wypełniamy

| Aspekt | Co dziś jest w BTerminalu |
|---|---|
| REST API samego procesu | **Brak.** Pluginy mają własne (RemoteControll :7777, sidecary :876x), sam BTerminal — zero |
| Testy | **Brak.** Żadnego `tests/`, żadnego CI |
| Flagi CLI | **Brak.** `main()` (`bterminal.py:10319`) używa wyłącznie `Gtk.Application` bez `argparse` |
| Punkty wejścia GTK do automatyzacji | Już są: `add_local_tab` (l. 9428), `open_ssh_tab` (l. 9474), `open_claude_tab` (l. 9501), `close_tab` (l. 9528), `toggle_sidebar` (l. 9593), `toggle_git_panel` (l. 9646), `terminal.feed_child(bytes)` (l. 2565). Stabilna powierzchnia — nadaje się do wystawienia 1:1 |
| Introspekcja | `self._plugins` dict, `self.notebook.get_nth_page(i)` — wszystko czytelne z poziomu Pythona |

Wniosek: **mamy zero infrastruktury testowej, ale mamy gotowe API
wewnętrzne** (publiczne metody `BTerminalApp`). Wystawienie ich przez REST
to ~300 linii w `bterminal.py` plus moduł testów.

### Cel funkcjonalny

1. **Móc z zewnątrz (z Claude'a, z `pytest`, z agent-testera) wywoływać
   akcje na żywym BTerminalu** — otworzyć zakładkę, wstrzyknąć tekst,
   przełączyć plugin, zrobić screenshot
2. **Móc napisać smoke-testy** całej aplikacji — uruchom BTerminal,
   wywołaj scenariusz, zweryfikuj stan, ubij. Bez tego refaktor sidecarów
   jest hazardem
3. **Zachować bezpieczeństwo produkcyjne** — w wersji wydanej REST debug
   musi być **niemożliwy do włączenia bez świadomej akcji użytkownika**

### Model bezpieczeństwa

Domyślnie OFF. Włączany **wyłącznie** przez świadomą akcję — i to wprost,
nie ukryte w opcjach.

| Warstwa | Mechanizm |
|---|---|
| **Aktywacja** | Flaga CLI `--debug-rest` LUB env `BTERMINAL_DEBUG_REST=1`. Brak w opcjach GUI w pierwszej iteracji (zbyt łatwo zostawić włączone) |
| **Bind** | Wyłącznie `127.0.0.1:7780` (loopback). Brak `0.0.0.0`, brak konfiguracji adresu |
| **Auth** | Token bearer w `Authorization: Bearer <token>`. Token generowany **przy każdym starcie** (`secrets.token_urlsafe(32)`), zapisywany do `~/.config/bterminal/debug_token` z `chmod 600`. Klient czyta plik, BTerminal weryfikuje string-compare. Brak — 401 |
| **Whitelist** | Sztywna lista endpointów. Brak `eval`, brak `exec`, brak generycznego "send-key", tylko nazwane akcje. Endpoint `/api/window/screenshot` nigdy nie ujawnia treści innych okien — tylko `BTerminalApp` |
| **Audit log** | Każde wywołanie (timestamp, endpoint, wynik) → `~/.cache/bterminal/debug-rest.log`. Append-only, rotacja przy 10 MB |
| **Idle auto-off** | 30 min bez requestu → REST server gaśnie. Restart wymaga ponownego uruchomienia z flagą |
| **Wizualny marker** | Stałe ostrzeżenie że REST jest otwarty — żeby user (lub ktoś podchodzący do kompa) nigdy nie zapomniał. Decyzja: **(a) suffix `[DEBUG-REST :7780]` w title barze + (b) cienki czerwony pasek 2px nad notebookiem**. (a) widać w window list i alt-tab; (b) widać peripheral vision podczas pracy. Bez agresywnego banera w środku UI — nie chcę przeszkadzać developerowi który świadomie używa trybu |
| **Destruktywne akcje** | `POST /api/quit`, `POST /api/tabs/*/close` z `?force=true` — wymagają flagi w request body, **nie** są domyślne |

Drugi tryb — **`--readonly-rest`** (`:7781`) — bez tokena, tylko GET-y
(state, tabs, plugins, screenshot). Do skryptów monitorujących bez ryzyka.
Też off by default. Decyzja czy go robimy w tym branchu — opcjonalna.

### Powierzchnia API (szkielet)

```
GET  /api/health                            — {ok, version, debug_mode, idle_seconds}
GET  /api/state                             — snapshot: {tabs[], plugins{}, sidecars{}, options{}}
GET  /api/tabs                              — [{idx, type, title, claude_config, task_project}]
POST /api/tabs/local                        — open_local_tab → {idx}
POST /api/tabs/ssh    {session_name}        — open_ssh_tab z istniejącej konfiguracji
POST /api/tabs/claude {claude_config_name}  — open_claude_tab
POST /api/tabs/{idx}/close                  — close_tab (wymaga ?force=true gdy task aktywny)
POST /api/tabs/{idx}/feed   {text}          — terminal.feed_child(text.encode())
POST /api/tabs/{idx}/key    {key}           — wyślij klawisz (whitelist enter/tab/esc/ctrl-c)
GET  /api/tabs/{idx}/screenshot             — PNG ramki VTE (Gdk.Window.get_pixbuf)
GET  /api/window/screenshot                 — PNG całego okna BTerminala
POST /api/window/toggle_sidebar
POST /api/window/toggle_git_panel
GET  /api/plugins                           — lista GTK pluginów + enabled + loaded
POST /api/plugins/{name}/enable             — hot enable (Etap 4)
POST /api/plugins/{name}/disable            — hot disable
GET  /api/sidecars                          — lista sidecarów + /health status
POST /api/sidecars/{name}/start             — SidecarRunner.start
POST /api/sidecars/{name}/stop              — SidecarRunner.stop
GET  /api/sidecars/{name}/health            — proxy do healthcheck_url
GET  /api/debug/log                         — ogon audit logu (ostatnie 200 wpisów)
POST /api/quit                              — graceful shutdown (wymaga ?confirm=true)
```

Wszystkie akcje mutujące GTK-stan idą przez `GLib.idle_add(callable)`,
żeby trafić do GTK main loop. Wynik wraca przez `queue.Queue` z timeoutem 5s.

### Mechanika testowania

`tests/` w repo (pierwszy raz). Stos: `pytest` + `httpx` + `Pillow`
(do diff screenshotów).

**Fixture `bterminal_process`:**
1. `subprocess.Popen(["python", "bterminal.py", "--debug-rest"], env={"DISPLAY": ":99"})` —
   `:99` = Xvfb headless dla CI
2. Czekaj na `GET /api/health` (max 10s)
3. Czytaj token z `~/.config/bterminal/debug_token`
4. Yield klient (`httpx.Client` z `Authorization: Bearer <token>`,
   `base_url="http://127.0.0.1:7780"`)
5. Po teście: `POST /api/quit?confirm=true`, fallback `process.terminate()`

**Smoke-testy obowiązkowe (każdy commit do brancha):**
- `test_health_returns_ok`
- `test_can_open_close_local_tab` — sanity loop
- `test_remote_controll_loads_with_get_session_context` — RemoteControll
  GTK plugin nie pęka po dodaniu sidecar runtime
- `test_sidecar_btmsg_starts_and_health_ok` — sidecar lifecycle
- `test_plugin_hot_disable_removes_from_sidebar` — Etap 4 weryfikacja
- `test_per_tab_enabled_plugins_filter_intro_prompt` — Etap 4 weryfikacja
- `test_quit_without_confirm_returns_400`
- `test_unauthorized_request_returns_401`

**Integracja z agent-tester:**
Agent-tester potrafi testować dowolny serwis HTTP. BTerminal z aktywnym
debug-REST staje się jednym z możliwych targetów. Graf agent-testera może
mieć węzły: "open Claude tab → feed prompt → screenshot → assert".
Ten use-case projektujemy jako możliwość, **nie** implementujemy w tym
branchu — chcę tylko żeby API debug-REST nadawało się do tego.

---

## Plan etapów (FINALNY — debug-REST PIERWSZY, używany do self-testowania reszty)

Zmiana strategiczna z rundy 3: **debug-REST powstaje jako pierwszy** —
zanim ruszą sidecary. Powód: model (Claude) używa go do screenshot-driven
self-testu każdego kolejnego etapu (screenshot przed akcją → akcja →
screenshot po → ocena wizualna). Bez tego narzędzia rozwój sidecarów
i korelacji zakładka↔plugin polega na ręcznych testach usera, co spowalnia
iterację i miesza role.

| Etap | Co | Test akceptacyjny | Self-test sposobem |
|---|---|---|---|
| **0** | Branch + ten README | (zrobione, commits `015a42f`, `04a1087`, `903591d`, `<ten>`) | — |
| **1 — debug-REST szkielet** | Flaga `--debug-rest` + env override; `BTerminalDebugServer` (`http.server` w wątku); token gen + zapis `~/.config/bterminal/debug_token` chmod 600; audit log `~/.cache/bterminal/debug-rest.log`; idle auto-off 30 min; wizualny marker (title bar suffix + 2px czerwony pasek nad notebookiem) | `bterminal --debug-rest` startuje, suffix widoczny, `curl -H "Authorization: Bearer $(cat ~/.config/bterminal/debug_token)" http://127.0.0.1:7780/api/health` → `{ok, version, debug_mode: true, idle_seconds}`. Bez flagi: port 7780 zamknięty, suffix nieobecny | Manualny: porównanie screenshotów title bara z `--debug-rest` i bez. Pierwszy test screenshotem przez `gnome-screenshot` lub `import` (bo `/api/window/screenshot` jeszcze nie istnieje) |
| **2 — read-only endpointy** | `/api/state`, `/api/tabs`, `/api/plugins`, `/api/sidecars` (zwraca `[]` na razie), `/api/window/screenshot` (PNG całego okna przez `Gdk.Window.get_pixbuf`), `/api/debug/log`. Wszystko przez `GLib.idle_add` + `Queue` | Każdy endpoint zwraca poprawny JSON/PNG. Screenshot pokazuje BTerminala z 0 zakładkami — porównywalny z manualnym `gnome-screenshot` | **Pierwszy prawdziwy self-test:** Claude curlem robi `GET /api/window/screenshot`, czyta plik tooliem `Read`, opisuje co widzi |
| **3 — mutujące endpointy (na istniejącym API)** | `/api/tabs/local` (POST → `add_local_tab`), `/api/tabs/{idx}/close` (`?force=true` gdy task), `/api/tabs/{idx}/feed`, `/api/tabs/{idx}/key` (whitelist enter/tab/esc/ctrl-c/letter), `/api/window/{toggle_sidebar,toggle_git_panel}`, `/api/quit?confirm=true`. Endpointy plugin/sidecar zwracają **501 Not Implemented** (uruchomimy je w późniejszych etapach) | Sekwencja curl: open local tab → screenshot (zakładka widoczna) → feed `"echo hello\n"` → screenshot (output widoczny) → close tab → screenshot (znów 0 zakładek) | **Główny self-test loop:** Claude wykonuje sekwencję, robi 4 screenshoty, czyta każdy, weryfikuje progresję. Jeśli któryś nie pokazuje oczekiwanego stanu — bug w endpoincie |
| **4 — tests scaffold (wczesny)** | `tests/conftest.py` z fixture `bterminal_process` (spawn pod `xvfb-run`); pierwsze 4 smoke-testy: `test_health`, `test_unauthorized_returns_401`, `test_open_close_local_tab`, `test_quit_without_confirm_400` | `pytest tests/` zielone na świeżym Xvfb. Runtime < 15s | Smoke ręcznie + uruchomienie `pytest` z konsoli — pierwsza automatyzacja |
| **5 — sidecar runtime** | `SidecarDiscovery`, `SidecarRunner` (`subprocess.Popen` z `start_new_session=True`), `HealthChecker`, lokalizacja `~/.config/bterminal/sidecars/*.json`, `atexit` cleanup. Wpięcie do `BTerminalApp.__init__` (discovery only, bez auto-startu) | BTerminal startuje, lista wykrytych sidecarów w logach, RemoteControll i wbudowane panele bez zmian. `/api/sidecars` zwraca prawdziwą listę (nie pustą) | Self-test: screenshot przed dodaniem manifestu → restart BTerminala → screenshot → `/api/sidecars` (curl) — porównanie wyniku |
| **6 — sidecar mutujące endpointy** | `/api/sidecars/{name}/{start,stop,health}` — przestają być 501. `SidecarRunner.start/stop` po stronie GTK | curl start `btmsg` → port :8766 nasłuchuje (`ss -tlnp \| grep 8766`) → curl `/api/sidecars/btmsg/health` → `{ok}` → curl stop → port wolny | Self-test: sekwencja curli, weryfikacja przez `ss -tlnp` (też przez REST jeśli dorzucimy `/api/debug/ports`) |
| **7 — manifesty 5 sidecarów** | Manifesty dla `btmsg` :8766, `explorer` :8770, `mr`, `taskboard`, `agent-tester` :8081 w `~/.config/bterminal/sidecars/`. Wszystkie z `auto_start: false`. `cwd` ustawione na root agent_controllera/agent-testera | Każdy z 5 startuje przez REST `/api/sidecars/{name}/start`, `/health` ok | Self-test: dla każdego — start → screenshot okna BTerminala (czy się nie zawiesił) → curl health → stop |
| **8 — korelacja zakładka↔plugin (KLUCZ)** | (a) Hot toggle GTK pluginów w `_on_enabled_toggled` — bez restartu, faktyczne `activate()/deactivate()`. (b) `TerminalTab.enabled_plugins: set[str] \| None`. (c) Checkbox-lista pluginów w `ClaudeCodeDialog`. (d) Pętla intro-promptu (l. 2502) filtruje po `tab.enabled_plugins`. (e) Refcount sidecarów: start przy pierwszej zakładce, stop gdy ostatnia odpina. (f) `/api/plugins/{name}/{enable,disable}` przestają być 501 | Dwie zakładki Claude przez REST, jedna z `btmsg` druga bez. Hot disable RemoteControll bez crashu. Refcount: zamknięcie ostatniej zakładki używającej `btmsg` → port :8766 wolny | Self-test: `POST /api/tabs/claude` (config A z btmsg), screenshot, `POST /api/tabs/claude` (config B bez btmsg), screenshot, `POST /api/plugins/remote_controll/disable`, screenshot panel-strona zniknął, `POST /api/plugins/remote_controll/enable`, screenshot wrócił |
| **9 — intro prompt sidecarów** | `prompt` z manifestu sidecara doklejany do intro Claude, filtrowany przez `tab.enabled_plugins` | Intro prompt nowej sesji Claude (czytalny przez `/api/tabs/{idx}/intro_prompt` — nowy endpoint) zawiera sekcje tylko aktywnych w niej sidecarów | Self-test: porównanie intro promptu zakładki z btmsg vs bez (diff) |
| **10 — komplet smoke-testów** | Dolne 4 testy: `test_remote_controll_loads`, `test_sidecar_btmsg_lifecycle`, `test_plugin_hot_disable`, `test_per_tab_enabled_plugins_filter` | Komplet 8 testów zielonych. CI-ready (jeśli kiedyś dorzucimy GitHub Actions) | `pytest tests/ -v` |
| **11** | (opcjonalne, pomijamy zgodnie z decyzją) `--readonly-rest :7781` | — pominięte — | — |
| **12** | (opcjonalne) `SidecarStatusPanel` w sidebarze (lista, /health, restart) | Można odpuścić |  |

### Konsekwencje zmiany kolejności

- **Zysk:** Każdy etap 5–9 ma natychmiastowy self-test wizualny.
  Mniej ręcznego "uruchom i sprawdź" po Twojej stronie.
- **Koszt:** debug-REST szkielet powstaje **przed** funkcjonalnościami,
  które ma testować — niektóre endpointy są stubami (501) na początku
  i ożywają w późniejszych etapach. To jest OK, dokumentowane przez 501.
- **Endpointy mutujące dla pluginów/sidecarów (ETAP 6 i 8 oryginalne)
  zostały rozbite** na podetapy — implementujemy je dopiero gdy
  odpowiednie GTK API istnieje. Nie ma "martwego" kodu.

### Self-test loop — konwencja dla każdego etapu po Etapie 2

```
1. Claude robi screenshot stanu PRZED akcją (curl /api/window/screenshot, Read PNG)
2. Claude opisuje co widzi (1-2 zdania)
3. Claude wywołuje akcję (curl POST /api/...)
4. Claude robi screenshot stanu PO akcji
5. Claude porównuje — co się zmieniło, czy zgodnie z oczekiwaniem
6. Jeśli zgodnie: commit. Jeśli nie: debug, fix, powtórz
```

Wynik tej pętli (krótki opis + 2 ścieżki do PNG) idzie do commit message
albo do osobnego pliku `docs/etap-N-selftest.md`. Decyzja czy zachowujemy
PNG-i w repo — pewnie nie (rozmiar), tylko opis tekstowy.

### Notatka o kosztach (zaktualizowana)

| Blok | Linie kodu (szac.) |
|---|---|
| Debug-REST szkielet (Etap 1) | 250-300 |
| Read-only endpointy (Etap 2) | 150 |
| Mutujące endpointy istniejące (Etap 3) | 200 |
| Test scaffold + 4 smoke (Etap 4) | 150 |
| Sidecar runtime (Etap 5) | 200 |
| Sidecar endpointy + manifesty (Etapy 6-7) | 100 |
| Korelacja zakładka↔plugin (Etap 8) | 250 |
| Intro prompt + 4 smoke (Etapy 9-10) | 150 |
| **Suma** | **~1500 linii** w `bterminal.py` + `~250` w `tests/` |

Jeden PR, etapy commitowane sekwencyjnie z self-test screenshots opisanymi
w commit messages.

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

## Decyzje przyjęte (runda 3)

| # | Decyzja | Konsekwencja |
|---|---|---|
| 1 | Port `:7780` ok | Stały port w manifestach/dokach |
| 2 | **Debug-REST = jeden PR z sidecarami i jest narzędziem developmentu** | Etap 1-3 to debug-REST przed sidecarami, dalsze etapy używają go do self-testu (screenshot przed/po akcji) |
| 3 | `--readonly-rest` odpuszczamy | Wykreślone z planu |
| 4 | **Wizualny marker = title bar suffix `[DEBUG-REST :7780]` + 2px czerwony pasek nad notebookiem** | (a) widać w window list i alt-tab, (b) widać peripheral vision podczas pracy. Bez agresywnego banera w środku UI |

### Wytłumaczenie pkt 4 (wizualny marker — po co)

Z `--debug-rest` ktokolwiek **z lokalnej maszyny** (każdy proces który
może czytać `~/.config/bterminal/debug_token`) ma władzę nad BTerminalem:
otwarcie zakładek SSH, wstrzyknięcie tekstu do shella, zamknięcie aplikacji,
toggle pluginów. To duży przywilej.

Bez markera: uruchamiasz `--debug-rest`, zaczynasz pracę, zapominasz, idziesz
na obiad. Komputer wygląda dokładnie tak samo jak przy zwykłym BTerminalu.
Z markerem: za każdym razem gdy patrzysz na okno — title bar i czerwony
pasek krzyczą "REST OTWARTE". Niemożliwe do przeoczenia, ale też niebanalne
dla developera (nie blokuje UI, nie wymaga klikania).

## Pytanie otwarte przed Etapem 1

**Per-zakładka opt-in domyślnie ON, OFF, czy per-plugin z manifestu?** —
patrz wyżej w sekcji "Decyzje przyjęte (runda 2)". Skłaniam się do
wariantu **C** (per-plugin `default_in_session` w manifeście). Jeśli
akceptujesz — ruszam Etap 1 (debug-REST szkielet).
