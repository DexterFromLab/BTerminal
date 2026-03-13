# BTerminal

GTK 3 terminal with SSH & Claude Code session management, macros, cross-session context database, AI model consultation via OpenRouter, and task management with auto-trigger. Catppuccin Mocha theme.

![BTerminal](screenshot.png)

## Features

- **SSH sessions** — saved configs (host, port, user, key, folder, color), one-click connect from sidebar
- **Claude Code sessions** — saved configs with sudo askpass, resume, skip-permissions and initial prompt
- **SSH macros** — multi-step automation (text, key press, delay) bound to sessions, runnable from sidebar
- **Tabs** — multiple terminals in tabs with reordering, auto-close and shell respawn
- **Folder grouping** — organize both SSH and Claude Code sessions in collapsible sidebar folders
- **Session colors** — 10 Catppuccin accent colors for quick visual identification
- **Sudo askpass** — temporary helper for Claude Code sudo mode: password entered once, auto-cleanup on exit
- **Catppuccin Mocha** — full theme across terminal, sidebar, tabs, dialogs and scrollbars

### Consult (AI Models)

- **Consult CLI** — query external AI models via OpenRouter from the terminal (`consult 'question'`)
- **Consult panel** — sidebar tab for model management: enable/disable models, set default, fetch available models from OpenRouter
- **Pipe support** — pipe any output to consult for analysis (`cat log.txt | consult 'what went wrong?'`)
- **File context** — attach files to queries (`consult -f code.py 'review this'`)

### Task Management

- **Tasks CLI** — per-project task lists (`tasks add project "description"`, `tasks done project id`)
- **Task List panel** — sidebar tab for browsing and managing tasks per project
- **Auto-trigger** — automatically trigger Claude Code sessions to pick up and execute pending tasks

### Context Manager

- **ctx CLI** — SQLite-based tool for persistent context across Claude Code sessions
- **Ctx Manager panel** — sidebar tab for browsing, editing and managing all project contexts
- **Ctx Setup Wizard** — step-by-step project setup with auto-detection from README and CLAUDE.md generation
- **Import / Export** — selective import and export of projects, entries, summaries and shared context via JSON with checkbox tree UI

## Installation

```bash
git clone https://github.com/DexterFromLab/BTerminal.git
cd BTerminal
./install.sh
```

The installer will:
1. Install system dependencies (python3-gi, GTK3, VTE)
2. Copy files to `~/.local/share/bterminal/`
3. Create symlinks: `bterminal`, `ctx`, and `consult` in `~/.local/bin/`
4. Initialize context database at `~/.claude-context/context.db`
5. Add desktop entry and icon to application menu

### Manual dependency install (Debian/Ubuntu/Pop!_OS)

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91
```

## Usage

```bash
bterminal
```

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
consult -m gpt-4 'question'         # Ask a specific model
consult -f code.py 'review this'    # Attach a file for context
cat log.txt | consult 'what failed?' # Pipe input for analysis
consult models                       # List available models
```

Configuration: `~/.config/bterminal/consult.json` (API key and model settings).

## Tasks

`tasks` manages per-project task lists for Claude Code sessions.

```bash
tasks list myproject                 # Show all tasks
tasks context myproject              # Show tasks + next task instructions
tasks add myproject "description"    # Add a task
tasks done myproject <task_id>       # Mark task as done
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
| `Ctrl+Shift+W` | Close tab |
| `Ctrl+Shift+C` | Copy |
| `Ctrl+Shift+V` | Paste |
| `Ctrl+PageUp/Down` | Previous/next tab |

## License

MIT
