# /check-deps — BTerminal dependency version check

Check all BTerminal dependencies against the manifest and report available updates.
Run this at the start of a BTerminal project session.

## Flow

### 1. Read the manifest

```bash
cat ~/.local/share/bterminal/defaults/dependencies.json
```

### 2. Check each category

Run all checks in a single bash block for speed:

```bash
echo "=== Runtime ==="
echo "python3: $(python3 --version 2>&1)"
echo "node:    $(node --version 2>/dev/null || echo 'NOT FOUND')"
echo "npm:     $(npm --version 2>/dev/null || echo 'NOT FOUND')"

echo ""
echo "=== Claude Code ==="
echo "installed: $(claude --version 2>/dev/null || echo 'NOT FOUND')"
echo "latest:    $(npm view @anthropic-ai/claude-code version 2>/dev/null || echo 'check failed')"

echo ""
echo "=== Node.js (apt) ==="
apt-cache policy nodejs 2>/dev/null | grep -E "Zainstalowany:|Installed:|Kandydat:|Candidate:" || echo "apt-cache unavailable"

echo ""
echo "=== System tools ==="
git --version 2>/dev/null || echo "git: NOT FOUND"
apt-cache policy git 2>/dev/null | grep -E "Zainstalowany:|Installed:|Kandydat:|Candidate:"
ssh -V 2>&1 | head -1 || echo "ssh: NOT FOUND"

echo ""
echo "=== GTK bindings ==="
apt-cache policy gir1.2-gtk-3.0 2>/dev/null | grep -E "Zainstalowany:|Installed:|Kandydat:|Candidate:"
apt-cache policy gir1.2-vte-2.91 2>/dev/null | grep -E "Zainstalowany:|Installed:|Kandydat:|Candidate:"
apt-cache policy python3-gi 2>/dev/null | grep -E "Zainstalowany:|Installed:|Kandydat:|Candidate:"
```

### 3. Format the report

Present results as a table. For each dependency:

| Symbol | Meaning |
|--------|---------|
| ✓ | Installed version meets requirement, no update available |
| ⚠ | Update available (Candidate != Installed, or npm latest != installed) |
| ✗ | Not installed or below minimum required version |

Example output format:

```
BTerminal v1.0.0 — Dependency Status
──────────────────────────────────────────
Runtime
  ✓ python3     3.12.3   (>= 3.10 required)
  ✓ node        v22.1.0  (>= 22.0 required)
  ⚠ npm         10.2.0   → 10.9.2 available

Claude Code
  ⚠ claude      2.1.100  → 2.1.118 available  [run: npm install -g @anthropic-ai/claude-code]

System tools
  ✓ git         2.43.0
  ✓ ssh         OpenSSH_9.6

GTK bindings
  ✓ GTK 3.0    (gir1.2-gtk-3.0)
  ✓ VTE 2.91   (gir1.2-vte-2.91)
  ✓ python3-gi
──────────────────────────────────────────
```

### 4. If updates are available

List the update commands concisely:

```
Updates available — run install.sh to apply all, or individually:
  npm install -g @anthropic-ai/claude-code   # claude-code
  sudo apt upgrade nodejs                     # node
```

Do NOT run the updates automatically. Only report and suggest.

### 5. If everything is up to date

```
✓ All dependencies up to date.
```

## Key principles

- Fast: all checks run in one bash call, no interactive prompts
- Read-only: never install or modify anything, only report
- Actionable: always show the exact command to fix any issue
- Concise: one line per dependency, no walls of text
