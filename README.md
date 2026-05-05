# BTerminal

A GTK 3 terminal emulator built for developers who work with SSH servers and Claude Code. Combines session management, macro automation, a persistent context database, multi-model AI consultation, task orchestration, git awareness, a skills library and a global rules system in a single window. Ships with Catppuccin Mocha (dark) and Latte (light) themes.

**Current release: v1.3.0**

![BTerminal](screenshot.png)

## Features

### Terminal

- Tabbed interface with VTE terminals, drag-to-reorder, and 10 000-line scrollback
- Local shell tabs (`Ctrl+T`) and SSH connections with saved configs (host, port, user, key file)
- Folder grouping for sessions in the sidebar with collapse, rename, move and ungroup
- Per-session accent colors (10 Catppuccin palette choices)
- Clipboard image detection on paste (`Ctrl+Shift+V`) — saves the image to `copied_images/` in the project directory and pastes the path; right-click option to paste directly into ctx
- Drag-and-drop file URIs into the terminal to paste paths

### Claude Code

- Saved Claude Code session configs: project directory, initial prompt, sudo askpass, resume flag, permission skip
- Sudo elevation via a temporary `SUDO_ASKPASS` helper — password entered once, retried on failure, cleaned up on exit
- Session metrics bar showing live duration, prompts, responses, tokens, cache hit rate, cost and throughput (parsed from Claude Code JSONL output)
- **Usage limits bar**: session (5 h) and weekly (7 d) utilization percentages fetched from the Anthropic OAuth API, refreshed every 60 s
- Emoji-tagged tabs for quick visual identification across multiple sessions
- "Open with" context menu — open a project directory in File Manager, VS Code, Zed or a custom command
- **BTerminal environment header** injected into every session's intro prompt so the agent knows it is working inside BTerminal and where to find the README

### Git Panel

A right-side panel that appears only on Claude Code tabs (`Ctrl+G` to toggle). Auto-refreshes every 3 seconds and monitors `.git/` for changes.

Accordion sections: **Branch** (current branch + HEAD), **Changes** (unstaged/untracked with numstat), **Stash**, **LFS/Binary** (detection + setup), **Activity** (recent commits), **Log** (last 20 oneline entries). Includes a `git init` button for uninitialized repos.

### SSH Macros

Multi-step automation sequences bound to sessions. Each step can be a text input, key press (Enter, Tab, Escape, Ctrl+C, Ctrl+D) or a timed delay. Steps are drag-reorderable in the editor and executed sequentially with 50 ms spacing.

### Context Manager (ctx)

SQLite-backed persistent context that survives across Claude Code sessions. Uses FTS5 full-text search and WAL journal mode.

```bash
ctx init myproject "description" /path/to/project
ctx get myproject                      # load project context
ctx get myproject --shared             # include shared (global) entries
ctx set myproject key "value"          # store an entry
ctx append myproject key "more"        # append to an existing entry
ctx shared set preferences "value"     # global context for all projects
ctx summary myproject "what was done"  # save session summary
ctx search "query"                     # full-text search
ctx list                               # list projects
ctx history myproject                  # session history
ctx export                             # export all data as JSON
ctx delete myproject [key]             # delete project or entry
ctx --help
```

The sidebar **Ctx** tab provides a tree view of all projects and entries, a detail/image preview pane, add/edit/delete operations, and selective import/export via JSON with a checkbox UI. A Setup Wizard walks through project registration and can auto-generate `CLAUDE.md`.

Images can be dragged into the ctx tree — they are stored in `~/.claude-context/images/` and indexed in the database.

### Memory & Rules

The sidebar **Memory** tab manages context rules injected periodically into Claude Code sessions. Rules fire every N prompts (default: 100) and can reference the ctx database, tool instructions and project-specific notes.

- **Global default rules** are shipped in `defaults/global_rules.txt` inside the repo and injected at the top of every session. Lines beginning with `#` are disabled. They update automatically with `git pull` — no reinstall needed.
- **Project rules** are stored per-project in the ctx database.
- `/reflect` — bundled skill: stops the agent, runs the `memory_wizard --dry-run` analysis, presents proposed rule changes, and asks for approval before applying.

```bash
memory_wizard                          # interactive wizard
memory_wizard --dry-run                # show proposals, no changes applied
memory_wizard --auto                   # auto-accept ADD-only proposals
```

### Skills

The sidebar **Skills** tab lists all installed Claude Code skills (markdown files in `~/.claude/commands/`). Bundled skills are marked with 📦.

**Bundled skills** (installed to `~/.claude/commands/` on first install):

| Skill | Description |
|-------|-------------|
| `/reflect` | Stop → analyze behavior → propose rule changes → apply with approval |
| `/check-deps` | Check all BTerminal dependencies against `dependencies.json`, report available updates |

Skills are never overwritten on update — user edits are preserved. New bundled skills are added on reinstall only if the file does not already exist.

### Files

The sidebar **Files** tab is a project file browser similar to the IntelliJ project tree.

- **Project dropdown** — lists all saved Claude Code sessions with a `project_dir`; defaults to the active Claude Code tab. Switching the dropdown pins the tree to that project.
- **Auto git root** — if the session's project directory is a generic subdirectory (`docs/`, `src/`, `tests/`, etc.), the panel automatically walks up to the nearest git root and shows the full project.
- **Double-click a file** — opens a diff dialog in meld
- **Double-click a directory** — expand / collapse

**Diff dialog** (meld):
- Dropdown with the last 10 commits (short hash + subject)
- Text field for any custom ref: full hash, branch name, `HEAD~5`, etc.
- Extracts the historical version via `git show` and opens `meld <old> <current>`

**Right-click context menu:**

| Option | Action |
|--------|--------|
| Open in Meld | Open file/directory directly in meld |
| Diff with commit… | Show the diff dialog |
| Open With ▸ | Submenu: Default App, VS Code, Zed, gedit, kate, Custom… |
| Copy Path | Full absolute path to clipboard |
| Copy Relative Path | Path relative to project root |
| Copy Name | Filename only |
| Paste Path to Terminal | Types the path into the active terminal |

Requires `meld` — installed automatically by `install.sh`.

### Extensions

Extensions are full tools or skill suites installed into `~/.local/share/bterminal/extensions/`. They are tracked in `defaults/dependencies.json`.

**Bundled extensions** (installed separately):

| Extension | Description |
|-----------|-------------|
| `latex-document-skill` | 27 LaTeX templates, compilation, PDF operations, format conversion — `/latex` skill |

The LaTeX extension is installed and pinned to a verified commit automatically by `install.sh`. No manual steps needed.

### Consult (AI Models)

Query external AI models through [OpenRouter](https://openrouter.ai) from the terminal or the sidebar panel.

```bash
consult "question"                     # ask the default model
consult -m google/gemini-2.5-pro "q"   # specific model (full ID with provider prefix)
consult -f code.py "review this"       # attach a file
cat log.txt | consult "what failed?"   # pipe input
consult models                         # list available models
```

The sidebar **Consult** tab manages API keys, enables/disables individual models, sets the default, and fetches the latest model list from OpenRouter. Supports both OpenRouter models and Claude Code native models (Opus, Sonnet, Haiku).

#### Tribunal (Multi-Model Debate)

Adversarial debate across multiple AI models with four roles: Analyst, Advocate, Critic and Arbiter. Configurable round count (1-6), single-pass mode, and per-project presets.

**Analyst** and **Arbiter** are always locked to `claude-code/opus` (full project file access + strongest model). **Advocate** and **Critic** are user-selectable OpenRouter models.

```bash
consult debate "problem"
consult debate "problem" \
  --advocate openai/gpt-5-codex \
  --critic deepseek/deepseek-r1
```

### Task Management

Per-project task lists with hierarchical IDs (1, 1.a, 1.b, 2, ...) and states: open, in_progress, completed.

```bash
tasks list myproject
tasks context myproject                # tasks + next-task instructions
tasks context myproject --session ID   # session-specific claiming
tasks add myproject "description"
tasks add myproject 1 "subtask"        # creates 1.a
tasks done myproject <task_id>
tasks pending myproject
tasks --help
```

The sidebar **Tasks** tab shows a per-project task list with checkboxes, add/edit/delete, and a 2-second auto-refresh poll. An **auto-trigger** system (start/stop buttons) continuously feeds pending tasks to Claude Code sessions — task claims are atomic to prevent collisions in multi-session setups. Auto-trigger flags reset to OFF on every app startup for safety.

### Plugins

Extend BTerminal with Python plugins loaded from `~/.config/bterminal/plugins/`. Each plugin can register a sidebar panel, keyboard shortcuts, and inject extra context into Claude Code session intro prompts.

- Plugins are single `.py` files or packages (directories with `__init__.py`)
- State (enabled/disabled) is persisted in `~/.config/bterminal/plugins.json`
- The sidebar **Plugins** tab lists installed plugins with version, author and status (Loaded / Disabled / Error), plus Add File / Add Folder / Remove actions
- Plugins have their own repositories — install them by cloning into `~/.config/bterminal/plugins/` or using the Add Folder button
- Changes to enable/disable state require a BTerminal restart

Full plugin API and a minimal example: [docs/plugin-spec.md](docs/plugin-spec.md).

### Errata & Auto-Update

On startup BTerminal checks `origin/master` for new commits. If an update is available it shows a prompt with the new commit list and admin message from `errata.json`. One click pulls + reinstalls and restarts automatically.

The update dialog shows a **live progress bar** and the current install step. If installation fails, the previous version is **automatically restored** — BTerminal continues to work and shows a user-friendly error message.

### Theme

Toggle between Catppuccin Mocha (dark) and Latte (light) with the sun/moon button. The switch re-colors the terminal palette, sidebar, tabs, dialogs and scrollbars live without restarting.

### Multi-Window

Launching `bterminal` while another instance is already running opens a new independent window instead of focusing the existing one.

### Languages

The UI ships in 13 languages, switchable live from **Options → Language**
without restarting:

> 🇬🇧 English &nbsp; 🇵🇱 Polski &nbsp; 🇩🇪 Deutsch &nbsp; 🇪🇸 Español &nbsp;
> 🇫🇷 Français &nbsp; 🇮🇹 Italiano &nbsp; 🇵🇹 Português &nbsp; 🇷🇺 Русский &nbsp;
> 🇺🇦 Українська &nbsp; 🇨🇿 Čeština &nbsp; 🇨🇳 中文 &nbsp; 🇯🇵 日本語 &nbsp; 🇰🇷 한국어

Default is **Auto-detect** (resolves from `LANGUAGE` / `LANG` env vars
with `en` fallback). Plural forms are language-correct: Slavic languages
(Polish, Russian, Ukrainian, Czech) get the proper 3-form (singular /
few / many), Romance and Germanic languages get 2-form, CJK languages
get 1-form.

The license dialog and update prompts also follow the active language —
each `defaults/license/LICENSE.<lang>.md` is a full translation, not a
placeholder. Switching language re-prompts for license acceptance once
per language (since the file's hash differs).

A toggle "Tell the AI agent which language I speak" (default ON) appends
a one-line hint to the Claude Code session intro prompt so the agent
responds in the user's language. The AI prompt itself stays English by
policy — only the hint identifies the user's preferred language.

## Requirements

- **Python 3.10+** with PyGObject, GTK 3 and VTE 2.91 bindings
- **Node.js 22+** and **npm 10+**
- **Claude Code** CLI — requires an active Claude subscription (Max or Pro); the installer sets it up automatically
- **OpenRouter account** *(optional)* — needed only for the Consult feature; requires API credits at [openrouter.ai](https://openrouter.ai)

## Installation

```bash
git clone https://github.com/DexterFromLab/BTerminal.git
cd BTerminal
./install.sh
```

The installer reads `defaults/dependencies.json` and enforces version requirements. It will:

1. Verify Python 3.10+, Node.js 22+, npm 10+ — upgrade Node via NodeSource if needed
2. Install or update Claude Code CLI via npm; create a stable symlink at `~/.local/bin/claude`
3. Install system tools: `git`, `ssh`, `meld`, `pandoc`, LaTeX tools (`pdflatex`, `latexmk`, `poppler-utils`) — all installed automatically via apt if missing
4. Install GTK bindings: `python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-vte-2.91`
5. Copy the `bterminal/` Python package (recursively) and CLI tools (`ctx`, `consult`, `tasks`, `claude_log`, `memory_wizard` from `tools/`) to `~/.local/share/bterminal/`, and create a `bterminal-launcher` shell script that runs `python3 -m bterminal`
6. Create live symlinks for `defaults/`, `README.md`, `VERSION` — `git pull` takes effect immediately, no reinstall needed
7. Install new bundled skills to `~/.claude/commands/` (never overwrites existing files)
8. Create symlinks in `~/.local/bin/`
9. Initialize the context database, write a desktop entry and update the icon cache

On critical errors the installer exits with code 1 and writes a summary to `~/.config/bterminal/install_errors.json` (shown as a dialog on next BTerminal startup). If an update fails mid-way, the previous working installation is automatically restored.

Use `./install.sh --no-sudo` for a non-root install (sets npm prefix to `~/.npm-global`).

### Manual dependencies (Debian / Ubuntu / Pop!_OS)

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91
```

## Usage

```bash
bterminal
```

The sidebar has eight built-in tabs: **Sessions**, **Ctx**, **Consult**, **Tasks**, **Memory**, **Skills**, **Files** and **Plugins**. Claude Code tabs also get a **Git panel** on the right. Installed plugins can add their own sidebar tabs.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+T` | New local shell tab |
| `Ctrl+Shift+W` | Close current tab |
| `Ctrl+Tab` | Next tab (wraps around) |
| `Ctrl+PageUp` / `Ctrl+PageDown` | Previous / next tab |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+G` | Toggle Git panel (Claude Code tabs) |
| `F5` | Refresh Git panel |
| `Ctrl+Shift+C` | Copy |
| `Ctrl+Shift+V` | Paste (detects clipboard images) |

## Configuration

Files in `~/.config/bterminal/`:

| File | Contents |
|------|----------|
| `sessions.json` | SSH sessions and macros |
| `claude_sessions.json` | Claude Code session configs |
| `consult.json` | OpenRouter API key, models and tribunal presets |
| `install_errors.json` | Last installer run: errors and warnings |
| `plugins.json` | Plugin enable/disable state |
| `debug_token` | Auth token for `--debug-rest` REST API (auto-generated) |
| `options.json` | Theme, font, shell, **language**, **tell_ai_language**, **license_accepted_hash**, **license_accepted_at** |

`options.json` keys related to i18n / license:

| Key | Default | Meaning |
|-----|---------|---------|
| `language` | `null` | UI locale code (`en`, `pl`, `de`, ...) or `null` for **Auto-detect** (LANGUAGE / LANG env). Set via Options dialog. |
| `tell_ai_language` | `true` | When ON and language ≠ `en`, appends a one-line user-language hint to AI session intro prompts. |
| `license_accepted_hash` | _(unset)_ | SHA-256 of the LICENSE the user accepted. Hash mismatch (license updated, language switched) triggers re-prompt on next launch. |
| `license_accepted_at` | _(unset)_ | ISO-8601 timestamp of acceptance. |

Context database: `~/.claude-context/context.db`

Global rules: `~/.local/share/bterminal/defaults/global_rules.txt` (symlink → repo)

Extensions: `~/.local/share/bterminal/extensions/`

## Architecture

Entry point: `python -m bterminal` (launcher: `~/.local/bin/bterminal` →
`bterminal-launcher` → `python3 -m bterminal`).

```
bterminal/                     ← Python package (~30 modules)
├── __init__.py                ← public API re-exports + helper injection
├── __main__.py                ← argparse + Gtk.Application bootstrap
├── app.py                     ← BTerminalApp orchestrator, ShrinkableBin
├── config.py                  ← paths, options, Catppuccin palette, CSS
├── models.py                  ← JsonListManager, SessionManager,
│                                ClaudeSessionManager, ConsultManager,
│                                SessionPasswordCache
├── debug_rest.py              ← loopback REST API (--debug-rest), 26 routes
├── plugin_runtime.py          ← BTerminalPlugin ABC (in-process contract)
├── sidecar_runtime.py         ← SidecarManifest, Discovery, Runner, HealthChecker
├── updater.py                 ← auto-update + errata + rollback
├── helpers.py                 ← intro prompt, clipboard, image attachments
├── ctx/
│   ├── helpers.py             ← project-name resolution, ctx CLI checks
│   ├── dialogs.py             ← CtxSetupWizard, CtxEditDialog
│   └── import_export.py       ← export/import dialogs
└── ui/
    ├── stats.py               ← SessionStatsBar (Claude-only)
    ├── sidebar.py             ← SessionSidebar
    ├── terminal_tab.py        ← TerminalTab + spawn_claude/spawn_ssh
    ├── dialogs/
    │   ├── sessions.py        ← SessionDialog, MacroDialog
    │   ├── claude_code.py     ← ClaudeCodeDialog + _build_intro_prompt
    │   └── options.py         ← OptionsDialog
    └── panels/                ← 8 sidebar panels: consult, ctx_manager,
                                  files, git, memory, plugin_manager,
                                  skills, tasks

tools/                         ← standalone CLI scripts (installed flat to
│                                ~/.local/share/bterminal/, symlinked to
│                                ~/.local/bin/)
├── ctx, consult, tasks, claude_log, memory_wizard
├── mock_ai_cli                ← test harness (provider-agnostic state machine)
├── test_all.sh                ← local test runner (unit/component/e2e/slow)
├── verify_e2e.sh              ← bash smoke test against running BTerminal
└── visual_demo.py             ← live UI demo (xdotool + screenshots)

docs/                          ← REQUIREMENTS, refactor docs, test coverage,
                                  plugin spec, exploration findings
```

See [`docs/refactor-modular-architecture.md`](docs/refactor-modular-architecture.md),
[`docs/refactor-test-plan.md`](docs/refactor-test-plan.md), and
[`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) for the full design + 66 R\<N\>
requirements with acceptance criteria.

## Testing

The full suite runs **locally**, without a VM. 207 tests, ~26 s wall-clock
on the dev machine.

```bash
./tools/test_all.sh             # fast (~16 s, 205 tests, skips slow)
./tools/test_all.sh --quick     # unit only (~0.3 s, 135 tests, no xvfb)
./tools/test_all.sh --slow      # full incl. exploration (~26 s, 207 tests)
./tools/test_all.sh --layer e2e # tests/e2e/ only (smoke battery + CLI tools)
./tools/test_all.sh --watch     # auto-rerun on changes (needs pytest-watch)
```

Local requirements:

- `python3 + pytest + httpx + Pillow` (`pip install pytest httpx Pillow`)
- `xvfb` for component / e2e layers (`apt install xvfb`)
- GTK 3 + VTE 2.91 (`apt install gir1.2-vte-2.91 gir1.2-gtk-3.0`)

### Test layers

| Layer | Count | Time | What it covers |
|-------|-------|------|----------------|
| **Unit** | 135 | 0.3 s | pure functions: `config`, `models`, `ctx.helpers`, plugin contracts, updater, password cache, legacy shim, entry-point |
| **Component** | ~40 | 5 s | REST integration — BTerminal subprocess under xvfb, REST endpoints, plugin lifecycle, sidecar manifests, audit log |
| **E2E** | ~30 | 10 s | full flows: smoke battery (16 panels/actions), `feed_capture` foundation, intro-prompt structure, CLI tools (ctx / tasks / consult / memory_wizard / claude_log), per-tab plugin gating |
| **Slow** | 3 | 10 s | random-walk exploration (1000 steps), idle timeout, btmsg sidecar |

The **`vte_capture`** fixture (in `tests/conftest.py`) records the bytes
BTerminal sends to the AI CLI (intro prompt, auto-trigger, rules injection,
ctx refresh) via `record_feed()` instrumentation + the
`/api/debug/feed_log` REST endpoint. **`bt_stderr_watcher`** is a
cursor-based stderr tail that catches `NameError` / `AttributeError` /
`TypeError` raised inside GTK signal callbacks — failures that would
otherwise be silently swallowed by GLib.

The **`mock_ai_cli`** harness in `tools/` replaces Claude with a scripted
provider-agnostic state machine, driven by JSON scenarios in
`tests/scenarios/`. This is what makes Claude Code flow tests reproducible
without a live API.

### Live UI demo (real display required)

```bash
# Machine with an X display (or VM with DISPLAY=:0):
./tools/visual_demo.py
```

Cycles through sidebar tabs, opens a Claude tab, types text via `xdotool`,
triggers rules injection, and writes screenshots to `/tmp/demo_*.png`. This
is a **live demo, not an automated test** — use it when you want to *see*
the flow end-to-end.

### Smoke check against a running instance

If BTerminal is already running with `--debug-rest`:

```bash
./tools/verify_e2e.sh
```

Requires the auth token at `~/.config/bterminal/debug_token`.

## Translating BTerminal

The UI is wired through GNU gettext. Source-of-truth translation files live
in `locale/<lang>/LC_MESSAGES/bterminal.po`; the runtime loads compiled
`.mo` files (built automatically by `install.sh` and gitignored).

### Currently shipped languages

| Code | Native | English | Plural forms |
|------|--------|---------|--------------|
| `en` | English   | English    | 2-form (canonical msgid) |
| `pl` | Polski    | Polish     | 3-form (singular / few / many) |
| `de` | Deutsch   | German     | 2-form |
| `es` | Español   | Spanish    | 2-form |
| `fr` | Français  | French     | 2-form |
| `it` | Italiano  | Italian    | 2-form |
| `pt` | Português | Portuguese | 2-form |
| `ru` | Русский   | Russian    | 3-form |
| `uk` | Українська| Ukrainian  | 3-form |
| `cs` | Čeština   | Czech      | 3-form |
| `zh` | 中文      | Chinese    | 1-form (invariant) |
| `ja` | 日本語    | Japanese   | 1-form |
| `ko` | 한국어    | Korean     | 1-form |

Each language ships:
- `locale/<code>/LC_MESSAGES/bterminal.po` — UI catalog (~75 strings)
- `defaults/license/LICENSE.<code>.md` — full legal translation

### Add or update a translation

```bash
./tools/i18n.sh extract        # rebuild locale/bterminal.pot from source
./tools/i18n.sh update         # propagate new/changed msgids into all .po
# ...edit each locale/<code>/LC_MESSAGES/bterminal.po (Poedit recommended)...
./tools/i18n.sh compile        # build .mo files for runtime
./tools/i18n.sh stats          # show translated / fuzzy / untranslated counts
```

For batch updates across all languages, edit `tools/_fill_translations.py`
(per-language Python dicts mapping msgid → msgstr) and run it — useful
when adding a new UI string and you want to fill all 12 translations
in one pass.

After editing, run the test suite — `tools/check_i18n.py` catches any
new Polish strings that were added without a `_()` / `N_()` wrapper, and
`tests/test_translations.py` parametrises every language for catalog
completeness, plural-form correctness, and license attribution:

```bash
./tools/test_all.sh --quick    # 314 tests, ~0.7s
```

### Add a new language

```bash
./tools/i18n.sh new sv_SE      # creates locale/sv/LC_MESSAGES/bterminal.po
# ...translate the .po file (or add a SV dict to tools/_fill_translations.py)...
./tools/i18n.sh compile
```

To make the new language selectable in **Options → Language**, add a
tuple to `SUPPORTED_LANGUAGES` in `bterminal/i18n.py`:

```python
SUPPORTED_LANGUAGES = (
    ("en", "English", "English"),
    ("pl", "Polski",  "Polish"),
    ...
    ("sv", "Svenska", "Swedish"),   # new
)
```

The third field is the **English name** of the language — it is what we
tell the AI agent when "Tell the AI agent which language I speak" is
enabled (the AI prompt itself stays English by policy).

CJK languages need a manual fix to the `Plural-Forms` header right after
`msginit` (it leaves `nplurals=INTEGER`):

```bash
sed -i 's|nplurals=INTEGER; plural=EXPRESSION;|nplurals=1; plural=0;|' \
    locale/<short>/LC_MESSAGES/bterminal.po
```

### Live language refresh

Switching the dropdown and clicking **Save** in Options re-installs the
gettext catalog and walks a registry of registered widgets to re-apply
their msgids in place — no restart needed. The mechanism lives in
`bterminal/i18n.py`:

- `register_translatable(widget, msgid, refresh)` — track a widget for
  later refresh; auto-cleaned via Gtk's `destroy` signal.
- `tr(widget, method, msgid)` — set + register in one call (the
  conventional helper).
- `tr_fmt(widget, method, template, **placeholders)` — same with
  `.format()` re-applied on every refresh.
- `refresh_translatables()` — fired after `init_locale()` to re-translate
  every live widget.

Set `BTERMINAL_I18N_DEBUG=1` to log every widget refresh to stderr —
useful when wiring a new UI surface.

### Translate the LICENSE

The license dialog displays a per-language file:

```
defaults/license/LICENSE.en.md   ← canonical (the EN dialog shows this)
defaults/license/LICENSE.pl.md   ← Polish
defaults/license/LICENSE.<code>.md   ← per-language translation
```

The license module falls back to `LICENSE.en.md` when no per-language
file exists, so adding a UI translation without a license translation
is safe — the user simply sees the English license.

When you change a `LICENSE.<lang>.md`, its SHA-256 changes and every
user on that locale will be re-prompted on next launch (this is also
why switching language re-prompts: different file = different hash).

### What is NOT translated (by policy)

- **AI intro prompt** and all "auto" prompts injected into Claude / Codex
  sessions — always English (the LLM speaks English natively; the
  user-language hint is appended only when "Tell the AI..." is on).
- **Installer output** (`install.sh`, `tools/i18n.sh`, ...) — always English.
- **`README.md`**, **`errata.json`** — always English.
- **Source comments and docstrings** — contributor's choice.

The audit script (`tools/check_i18n.py`) does not flag PL in installer /
docs / comments — only in user-facing UI strings inside `bterminal/`.

## License

Copyright (c) 2024-2026 **Bartosz Czarnota** &lt;<bartoszczarnota1@gmail.com>&gt;

BTerminal is provided under a custom license requiring **attribution**.
The full terms are in [LICENSE.md](LICENSE.md). In short: you may use,
modify, and redistribute the Software, but every copy and derivative
work must reproduce the original authorship information (author name,
contact email, project name) in the LICENSE file, an "About"-style UI
surface, and the primary documentation.

The application enforces license acceptance:

- on **first launch** after a fresh installation,
- **before each update** the user must accept the license again before
  the new version is installed.

Declining the dialog exits the application or aborts the update.
