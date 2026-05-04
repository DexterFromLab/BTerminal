"""BTerminal in-process plugin contract — `BTerminalPlugin` ABC.

External plugins (e.g., RemoteControll) inherit from this base class and
expose a `create_plugin(app)` factory at module level. The actual loader
that walks `~/.config/bterminal/plugins/` and imports modules is a method
of BTerminalApp (`_load_plugins`) — it stays there because it integrates
with sidebar_stack / switcher / shortcut routing.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/plugins/` package in a later migration etap.
"""


class BTerminalPlugin:
    """Base class for BTerminal plugins."""
    name = ""
    title = ""
    version = ""
    description = ""
    author = ""
    # Whether to enable this plugin by default in newly opened Claude Code
    # tabs. Subclasses can override to False for opt-in plugins (e.g. heavy
    # ones the user usually does not need). Used by Etap 8 per-tab plugin
    # selection in ClaudeCodeDialog.
    default_in_session = True

    def activate(self, app):
        return None

    def deactivate(self):
        pass

    def get_keyboard_shortcuts(self):
        return []

    def on_sidebar_shown(self):
        pass

    def get_session_context(self):
        """Return extra context string to inject into Claude Code intro prompt, or None."""
        return None
