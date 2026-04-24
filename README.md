# BTerminal

A GTK 3 terminal emulator built for developers who work with SSH servers and Claude Code. Combines session management, macro automation, a persistent context database, multi-model AI consultation, task orchestration, git awareness, a skills library and a global rules system in a single window. Ships with Catppuccin Mocha (dark) and Latte (light) themes.

**Current release: v1.1.7**

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
5. Copy `bterminal.py`, `ctx`, `consult`, `tasks`, `claude_log`, `memory_wizard` to `~/.local/share/bterminal/`
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

Context database: `~/.claude-context/context.db`

Global rules: `~/.local/share/bterminal/defaults/global_rules.txt` (symlink → repo)

Extensions: `~/.local/share/bterminal/extensions/`

## License

MIT
