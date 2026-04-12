# BTerminal Plugin Specification

## Overview

BTerminal plugins are Python modules placed in `~/.config/bterminal/plugins/`.
Each plugin can add a sidebar panel, register keyboard shortcuts, and hook into app lifecycle events.

## File Structure

A plugin is either:
- A single `.py` file: `~/.config/bterminal/plugins/my_plugin.py`
- A package directory: `~/.config/bterminal/plugins/my_plugin/__init__.py`

## Required Interface

Every plugin module **must** export a `create_plugin(app)` function that returns a `BTerminalPlugin` subclass instance:

```python
from bterminal import BTerminalPlugin

class MyPlugin(BTerminalPlugin):
    name = "my_plugin"           # unique ID (must match filename without .py)
    title = "My Plugin"          # display name in sidebar tab
    version = "1.0.0"            # semver
    description = "What this plugin does, shown in plugin manager detail area."
    author = "Your Name"

    def activate(self, app):
        """Called when the plugin is loaded.
        
        Args:
            app: BTerminalApp instance (Gtk.Window).
                 Access sidebar_stack, notebook, session_manager, etc.
        
        Returns:
            Gtk.Widget or None — if a widget is returned, it becomes
            a new sidebar panel tab. If None, the plugin runs headless.
        """
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # ... build your UI ...
        return panel

    def deactivate(self):
        """Called on app exit or plugin removal. Clean up resources."""
        pass

    def get_keyboard_shortcuts(self):
        """Return list of (modifier_mask, keyval, callback) tuples.
        
        Example:
            from gi.repository import Gdk
            return [(Gdk.ModifierType.CONTROL_MASK, Gdk.KEY_F9, self.do_something)]
        """
        return []

    def on_sidebar_shown(self):
        """Called when the plugin's sidebar tab becomes visible."""
        pass

    def get_session_context(self):
        """Return extra context to inject into Claude Code intro prompt.
        
        Called when a new Claude Code session is spawned.
        Return a string to append to the intro prompt, or None to skip.
        """
        return None


def create_plugin(app):
    return MyPlugin()
```

## Metadata Fields

| Field         | Type   | Required | Description                              |
|---------------|--------|----------|------------------------------------------|
| `name`        | str    | yes      | Unique ID, must match module/file name   |
| `title`       | str    | yes      | Human-readable name for sidebar tab      |
| `version`     | str    | no       | Semver string, shown in plugin manager   |
| `description` | str    | no       | Multi-line description for detail panel  |
| `author`      | str    | no       | Author name, shown in plugin list        |

## Available App References

Inside `activate(self, app)`, the `app` parameter is the `BTerminalApp` (Gtk.Window) instance. Key attributes:

| Attribute              | Type                 | Description                          |
|------------------------|----------------------|--------------------------------------|
| `app.notebook`         | Gtk.Notebook         | Main tab area with terminal tabs     |
| `app.sidebar_stack`    | Gtk.Stack            | Sidebar stack (Sessions, Ctx, etc.)  |
| `app.session_manager`  | SessionManager       | SSH session CRUD                     |
| `app.claude_manager`   | ClaudeSessionManager | Claude Code session CRUD             |
| `app.git_panel`        | GitPanel             | Git status/operations panel          |
| `app._plugins`         | dict                 | Loaded plugins {name: plugin}        |

## GTK3/UI Conventions

- All widgets use **Catppuccin** theme colors from the `CATPPUCCIN` dict
- Buttons: `btn.get_style_context().add_class("sidebar-btn")`
- Labels with ellipsize: `label.set_ellipsize(Pango.EllipsizeMode.END)`
- Container boxes: `spacing=4`, `set_border_width(6)`
- ScrolledWindow policy: `Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC`
- TreeView for lists with `Gtk.ListStore`
- After `show_all()`, newly created widgets need explicit `.show()` or `.show_all()`

## Imports

Plugins can import from the GTK3 stack:

```python
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk, Pango, Vte
```

Standard library modules are available. For `BTerminalPlugin` base class, import:

```python
# Option A: if bterminal is on sys.path (it usually is via the installed symlink)
from bterminal import BTerminalPlugin

# Option B: define the class inline (for standalone plugins)
class BTerminalPlugin:
    name = ""
    title = ""
    version = ""
    description = ""
    author = ""
    def activate(self, app): return None
    def deactivate(self): pass
    def get_keyboard_shortcuts(self): return []
    def on_sidebar_shown(self): pass
```

## Plugin Lifecycle

1. BTerminal starts, calls `show_all()`
2. `_load_plugins()` scans `~/.config/bterminal/plugins/`, reads `plugins.json` for enabled state
3. For each enabled plugin: `importlib` loads the module, calls `create_plugin(app)`
4. `_register_plugin()` calls `plugin.activate(app)`:
   - If a widget is returned: added to `sidebar_stack`, switcher button created
   - Keyboard shortcuts from `get_keyboard_shortcuts()` registered
5. On app close: `_unload_plugins()` calls `deactivate()` on each plugin

## Enable/Disable

Plugins can be enabled/disabled via the Plugins sidebar tab without deleting files.
State is stored in `~/.config/bterminal/plugins.json`:

```json
{
  "my_plugin": true,
  "other_plugin": false
}
```

Plugins not listed default to `true` (enabled).
Changes require BTerminal restart.

## Example: Minimal Plugin

```python
"""Hello World plugin for BTerminal."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class BTerminalPlugin:
    name = ""
    title = ""
    version = ""
    description = ""
    author = ""
    def activate(self, app): return None
    def deactivate(self): pass
    def get_keyboard_shortcuts(self): return []
    def on_sidebar_shown(self): pass


class HelloPlugin(BTerminalPlugin):
    name = "hello"
    title = "Hello"
    version = "0.1.0"
    description = "A minimal example plugin that shows a greeting."
    author = "BTerminal"

    def activate(self, app):
        self.app = app
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(6)
        label = Gtk.Label(label="Hello from a plugin!")
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)
        btn = Gtk.Button(label="Click me")
        btn.get_style_context().add_class("sidebar-btn")
        btn.connect("clicked", lambda _: label.set_text("Clicked!"))
        box.pack_start(btn, False, False, 0)
        return box

    def deactivate(self):
        pass


def create_plugin(app):
    return HelloPlugin()
```
