# BTerminal

GTK 3 terminal with SSH & Claude Code session management, macros, cross-session context database, AI model consultation via OpenRouter, multi-model debate (Tribunal), and task management with auto-trigger. Catppuccin Mocha/Latte themes with day/night toggle.

![BTerminal](screenshot.png)

## Features

- **SSH sessions** — saved configs (host, port, user, key, folder, color), one-click connect from sidebar
- **Claude Code sessions** — saved configs with sudo askpass, resume, skip-permissions, initial prompt and project directory
- **Session stats bar** — live metrics for Claude Code sessions: duration, prompts, responses, tokens, cache hit rate, cost estimate and throughput
- **SSH macros** — multi-step automation (text, key press, delay) bound to sessions, runnable from sidebar
- **Tabs** — multiple terminals in tabs with reordering, auto-close and shell respawn
- **Folder grouping** — organize both SSH and Claude Code sessions in collapsible sidebar folders with rename, move and ungroup
- **Session colors** — 10 Catppuccin accent colors with visual swatch picker
- **Open with** — right-click Claude Code sessions to open the project directory in File Manager, VS Code, Zed or a custom command
- **Git panel** — right-side panel for Claude Code tabs with accordion sections: Branch, Changes (with numstat), Stash, LFS/Binary, Activity and Log; auto-refresh every 3 s, file monitoring, `git init` button for uninitialized repos
- **Clipboard image paste** — `Ctrl+Shift+V` saves clipboard screenshots to `copied_images/` in the project directory and pastes the file path into the terminal; right-click menu option to paste image directly into ctx
- **Sudo askpass** — temporary helper for Claude Code sudo mode: password entered once, retry on wrong password, auto-cleanup on exit
- **Auto-update** — checks `origin/master` on startup, prompts to pull and reinstall if new version available
- **Day/night theme** — toggle between Catppuccin Mocha (dark) and Latte (light) with a single click; re-colors terminal, sidebar, tabs, dialogs and scrollbars live

### Consult (AI Models)

- **Consult CLI** — query external AI models via OpenRouter from the terminal (`consult 'question'`)
- **Consult panel** — sidebar tab for model management: enable/disable models, set default, fetch available models from OpenRouter
- **Pipe support** — pipe any output to consult for analysis (`cat log.txt | consult 'what went wrong?'`)
- **File context** — attach files to queries (`consult -f code.py 'review this'`)
- **Tribunal (debate)** — multi-model adversarial debate with configurable roles (Analyst, Advocate, Critic, Arbiter), per-project presets, and max rounds setting

### Task Management

- **Tasks CLI** — per-project task lists with hierarchical IDs (`tasks add project "description"`, `tasks done project id`)
- **Task List panel** — sidebar tab for browsing and managing tasks per project
- **Task claiming** — sessions atomically claim the next unclaimed task to prevent conflicts in multi-session setups
- **Auto-trigger** — start/stop buttons in the Task panel to continuously send pending tasks to Claude Code sessions until all are done; auto-trigger flags reset to OFF on app startup for safety

### Context Manager

- **ctx CLI** — SQLite-based tool for persistent context across Claude Code sessions
- **Ctx Manager panel** — sidebar tab for browsing, editing and managing all project contexts
- **Ctx Setup Wizard** — step-by-step project setup with auto-detection from README and CLAUDE.md generation
- **Import / Export** — selective import and export of projects, entries, summaries and shared context via JSON with checkbox tree UI

## Requirements

- **Python 3** with GTK 3 and VTE bindings
- **Claude Code** — active Claude subscription (Max or Pro plan); the installer will auto-install Claude Code CLI if not found
- **OpenRouter account** *(optional)* — required only for the Consult feature; needs API credits on [openrouter.ai](https://openrouter.ai)

## Installation

```bash
git clone https://github.com/DexterFromLab/BTerminal.git
cd BTerminal
./install.sh
```

The installer will:
1. Install system dependencies (python3-gi, GTK3, VTE)
2. Install Claude Code CLI via npm if not already present (installs Node.js if needed)
3. Copy files to `~/.local/share/bterminal/`
4. Create symlinks: `bterminal`, `ctx`, `consult` and `tasks` in `~/.local/bin/`
5. Initialize context database at `~/.claude-context/context.db`
6. Add desktop entry and icon to application menu

Use `./install.sh --no-sudo` for a non-root install (configures npm prefix at `~/.npm-global`).

### Manual dependency install (Debian/Ubuntu/Pop!_OS)

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91
```

## Usage

```bash
bterminal
```

The sidebar has four tabs: **Sessions** (SSH & Claude Code), **Ctx** (context manager), **Consult** (AI models & debate) and **Tasks** (task lists & auto-trigger). Claude Code tabs also get a **Git panel** on the right side (toggle with `Ctrl+G`).

## Context Manager (ctx)

`ctx` is a SQLite-based tool for managing persistent context across Claude Code sessions. It uses FTS5 full-text search and WAL journal mode.

```bash
ctx init myproject "Project description" /path/to/project
ctx get myproject                    # Load project context
ctx get myproject --shared           # Include shared context
ctx set myproject key "value"        # Save a context entry
ctx append myproject key "more"      # Append to existing entry
ctx shared set preferences "value"   # Save shared context (all projects)
ctx summary myproject "What was done" # Save session summary
ctx search "query"                   # Full-text search across everything
ctx list                             # List all projects
ctx history myproject                # Show session history
ctx export                           # Export all data as JSON
ctx delete myproject [key]           # Delete project or entry
ctx --help                           # All commands
```

### Ctx Manager Panel

The sidebar **Ctx** tab provides a GUI for the context database:

- Browse all projects and their entries in a tree view
- View entry values and project details in the detail pane
- Add, edit and delete projects and entries
- **Export** — select specific projects, entries, summaries and shared context to save as JSON
- **Import** — load a JSON file, preview contents with checkboxes, optionally overwrite existing entries

### Integration with Claude Code

Add a `CLAUDE.md` to your project root (the Ctx Setup Wizard can generate this automatically):

```markdown
On session start, load context:
  ctx get myproject

Save important discoveries: ctx set myproject <key> <value>
Before ending session: ctx summary myproject "<what was done>"
```

Claude Code reads `CLAUDE.md` automatically and will maintain the context database.

## Consult

`consult` queries external AI models via OpenRouter API.

```bash
consult 'question'                   # Ask the default model
consult -m model_id 'question'       # Ask a specific model (full ID, e.g. google/gemini-2.5-pro)
consult -f code.py 'review this'     # Attach a file for context
cat log.txt | consult 'what failed?' # Pipe input for analysis
consult models                       # List available models
```

### Tribunal (Multi-Model Debate)

```bash
consult debate "problem"             # Run a debate with default roles
consult debate "problem" \
  --analyst claude-code/opus \
  --advocate openai/gpt-5-codex \
  --critic deepseek/deepseek-r1 \
  --arbiter claude-code/opus         # Custom role assignment
```

The Consult panel in the sidebar provides a GUI for configuring debate roles, saving per-project presets and launching debates.

Configuration: `~/.config/bterminal/consult.json` (API key and model settings).

## Tasks

`tasks` manages per-project task lists for Claude Code sessions.

```bash
tasks list myproject                 # Show all tasks
tasks context myproject              # Show tasks + next task instructions
tasks context myproject --session ID # Session-specific task claiming
tasks add myproject "description"    # Add a task
tasks add myproject 1 "subtask"      # Add a subtask (hierarchical ID: 1.a)
tasks done myproject <task_id>       # Mark task as done
tasks pending myproject              # Count of open tasks
tasks --help                         # Full help
```

## Configuration

Config files in `~/.config/bterminal/`:

| File | Description |
|------|-------------|
| `sessions.json` | SSH sessions and macros |
| `claude_sessions.json` | Claude Code session configs |
| `consult.json` | Consult API key and model settings |

Context database: `~/.claude-context/context.db`

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+T` | New tab (local shell) |
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+Tab` | Next tab (wrap around) |
| `Ctrl+Shift+W` | Close tab |
| `Ctrl+G` | Toggle Git panel (Claude Code tabs) |
| `Ctrl+Shift+C` | Copy |
| `Ctrl+Shift+V` | Paste (image → save & paste path) |
| `Ctrl+PageUp/Down` | Previous/next tab |

## License

MIT
