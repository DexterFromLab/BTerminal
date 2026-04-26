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
| **Wizualny marker** | Gdy debug REST aktywny: title bar dodaje suffix `[DEBUG-REST]`, kolor menubara zmieniony — żeby nigdy nie zostawić włączonego niezauważenie |
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

## Plan etapów (zaktualizowany — z debug-REST i testami)

| Etap | Co | Test akceptacyjny |
|---|---|---|
| **0** | Branch + ten README | (zrobione, commit `015a42f` + `04a1087`) |
| **1** | `SidecarDiscovery` + `SidecarRunner` + `HealthChecker` jako klasy w `bterminal.py`. Manifest schema. Lokalizacja: `~/.config/bterminal/sidecars/*.json` | Smoke ręczny: 1 manifest, BTerminal startuje, sidecar wykryty, `start()`/`stop()` działa, `atexit` ubija |
| **2** | Wpięcie loadera do `BTerminalApp.__init__` (po `_load_plugins`). Bez auto-startu sidecarów | BTerminal startuje, lista wykrytych sidecarów w logach, RemoteControll i wbudowane panele dalej działają |
| **3** | Manifesty dla `btmsg`, `explorer`, `mr`, `taskboard`, `agent-tester` | Wszystkie 5 wykryte; ręczny start `btmsg` wstaje na :8766, `/health` ok |
| **4** | **Korelacja zakładka ↔ plugin** (kluczowy etap, bez zmian z poprzedniej rundy) | Dwie zakładki Claude, jedna z `btmsg` druga bez. Hot toggle bez restartu. Refcount sidecara. RemoteControll przeżywa hot disable/enable |
| **5** | `prompt` z manifestu sidecara doklejany do intro Claude, filtrowany per-tab | Intro prompt nowej sesji zawiera sekcje tylko aktywnych w niej sidecarów |
| **6** | **Debug-REST szkielet:** flaga `--debug-rest`, `BTerminalDebugServer` na :7780, token w `~/.config/bterminal/debug_token`, audit log, idle auto-off, wizualny marker `[DEBUG-REST]` w title barze | `bterminal --debug-rest` startuje, `curl -H "Authorization: Bearer $(cat ~/.config/bterminal/debug_token)" http://127.0.0.1:7780/api/health` zwraca `{ok, version, debug_mode: true}`. Bez flagi — port 7780 nie jest otwarty |
| **7** | **Debug-REST endpointy read-only:** `/api/state`, `/api/tabs`, `/api/plugins`, `/api/sidecars`, `/api/health`, `/api/window/screenshot`, `/api/debug/log`. Wszystko przez `GLib.idle_add` | Smoke z curl: każdy endpoint zwraca sensowne dane; screenshot to ważny PNG |
| **8** | **Debug-REST endpointy mutujące:** `/api/tabs/*` (open/close/feed/key), `/api/plugins/{name}/{enable,disable}`, `/api/sidecars/{name}/{start,stop}`, `/api/window/{toggle_sidebar,toggle_git_panel}`, `/api/quit?confirm=true`. Whitelist klawiszy, `?force=true` dla destruktywnych | Każda akcja wywołana przez REST powoduje obserwowalną zmianę w GUI. Bez `?confirm=true` quit zwraca 400 |
| **9** | **Tests scaffold:** `tests/conftest.py` z fixture `bterminal_process` (Xvfb-friendly), 8 obowiązkowych smoke-testów z listy powyżej | `pytest tests/` zielone na świeżym Xvfb. Test runtime < 30s |
| **10** | (opcjonalne) `SidecarStatusPanel` w sidebarze (lista, /health, restart). Można odpuścić |  |
| **11** | (opcjonalne) `--readonly-rest` na :7781 bez tokena, tylko GET. Decyzja na koniec |  |

Etapy 1–9 są w zakresie tego brancha. 10 i 11 opcjonalne.

### Notatka o kosztach

Etap 6+7+8 to ok. 400-500 linii w `bterminal.py` (zgodnie z konwencją
single-file). Etap 9 to ok. 200 linii w `tests/`. Łącznie + Etapy 1-5
szacuję na 1500-2000 linii zmian. Realne, ale duże — warto rozważyć
podzielenie merge'u na 2 PR (1-5 + 6-9).

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

## Pytania otwarte przed Etapami 6–9 (debug-REST)

1. **Port `:7780`** — pasuje, czy zarezerwowany u Ciebie?
   (Aktualne porty w użyciu: RemoteControll 7777, agent-tester 8081,
   ctx 8765, btmsg 8766, explorer 8770)
2. **Debug REST = osobny PR** czy razem z sidecarami w jednym mergu?
3. **`--readonly-rest` :7781** (tryb tylko-do-odczytu, bez tokena, do
   skryptów monitorujących) — robimy w tym branchu czy odpuszczamy?
4. **Wizualny marker `[DEBUG-REST]`** — title bar, kolor menubara, czy
   coś bardziej wyraźnego (np. czerwony pasek nad notebookiem)?

Po decyzji o A/B/C (sesja↔plugin) i powyższych — ruszam Etap 1.
