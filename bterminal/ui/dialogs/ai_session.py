"""AISessionDialog — provider-aware AI CLI session config dialog (T2.5).

Subclasses the legacy ClaudeCodeDialog (which keeps all the existing
fields: name / folder / project_dir / sudo / resume / skip_permissions
/ prompt / enabled_plugins) and prepends a provider dropdown.

T2.5 ships:
  - GtkComboBox at the top, populated from the ProviderRegistry.
  - On OK: get_data() returns the canonical R4.2 schema:
      {provider, name, folder, project_dir, color, prompt,
       enabled_plugins, provider_options: {resume, skip_permissions,
       sudo, model, ...}}
  - Edit mode: incoming session.provider preselects the dropdown;
    incoming session.provider_options is unfolded to top-level so the
    parent dialog's checkboxes pick up the values without changes.

T2.6 will replace the static Claude-specific checkboxes with dynamic
fields driven by `provider.get_dialog_schema()`. Until then the
parent's fields stay visible for both providers — Copilot tabs simply
get the same checkbox set, which is acceptable since `resume` /
`skip_permissions` map to Copilot's --resume / --yolo.

Pure helpers (testable without GTK):
    _build_provider_combo_items(registry)
    _split_provider_options_from_data(data)
    _flatten_session_for_legacy_dialog(session)
"""
from __future__ import annotations

from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from bterminal.ui.dialogs.claude_code import ClaudeCodeDialog


# Keys that historically lived on top-level of a session dict; in R4.2
# they belong under provider_options. Same list as models._LEGACY_PROVIDER_OPTION_KEYS
# kept in sync intentionally — both are part of the migration contract.
_PROVIDER_OPTION_KEYS = (
    "resume", "continue", "skip_permissions", "sudo", "model",
    "headless", "json_output", "allowed_tools", "plan_mode",
    "image_paste_template",
    # Task #3 (#75): Aider per-session override of the local-LLM
    # endpoint URL. Lets advanced users point one session at a remote
    # ollama / vLLM box without changing global config.
    "local_endpoint_url", "api_key",
)


# T4.3: Copilot's --allow-tool / --deny-tool grammar.
#   TOKEN          — whole tool category (e.g. "shell", "My-MCP-Server").
#   TOKEN(args)    — tool with specific argument pattern (e.g. "shell(rm)").
# Token is alpha + (\w / . / -). Args portion is opaque to BTerminal —
# Copilot itself parses them at runtime.
import re as _re

_ALLOWED_TOOL_TOKEN_RE = _re.compile(r"^[A-Za-z][\w.-]*$")


def is_valid_allowed_tool_rule(rule: str) -> bool:
    """Pure helper: True iff `rule` matches Copilot's allow/deny syntax.

    Examples (valid):
        shell, shell(rm), shell(rm -rf), My-MCP-Server, web.fetch,
        github.repo, my_tool(--flag value)

    Examples (invalid):
        ""                              empty
        "shell("                        unclosed paren
        "shell)"                        stray closing paren
        "1tool"                         must start with letter
        "shell(rm)x"                    trailing junk after closing paren
    """
    if rule is None:
        return False
    rule = rule.strip()
    if not rule:
        return False
    paren_idx = rule.find("(")
    if paren_idx == -1:
        return bool(_ALLOWED_TOOL_TOKEN_RE.fullmatch(rule))
    token = rule[:paren_idx]
    args_part = rule[paren_idx:]
    if not _ALLOWED_TOOL_TOKEN_RE.fullmatch(token):
        return False
    # Args portion must be the form "(...)" with at least one char inside
    # and no trailing junk.
    if not (args_part.startswith("(") and args_part.endswith(")")):
        return False
    inner = args_part[1:-1]
    if not inner.strip():
        return False
    return True


def parse_allowed_tools_text(text):
    """Pure helper: split a multi-line allowed-tools string into
    `{"valid": [...], "invalid": [(line_no, content), ...]}`.

    Empty lines and `#`-prefixed comments are silently dropped.
    Line numbers are 1-based for human-readable error messages.
    """
    if text is None:
        return {"valid": [], "invalid": []}
    valid = []
    invalid = []
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if is_valid_allowed_tool_rule(stripped):
            valid.append(stripped)
        else:
            invalid.append((i, stripped))
    return {"valid": valid, "invalid": invalid}


def _build_provider_combo_items(registry) -> list[tuple[str, str]]:
    """Return [(name, "{icon} {long_label}"), ...] for the dropdown.

    Task #11 (#83): consults registry.enabled() so providers the user
    hid via OptionsDialog → AI Providers don't appear when creating
    a new session. Existing sessions with a now-disabled provider
    keep rendering through registry.all() (sidebar / tab labels);
    only the 'create new' path filters.

    Pure helper (no GTK) so tests can verify the registry-driven
    population without instantiating the dialog.
    """
    if hasattr(registry, "enabled"):
        providers = registry.enabled()
    else:
        # Forward-compat: tests may pass a stub registry without
        # enabled() — fall back to all() rather than crashing.
        providers = registry.all()
    return [
        (p.name, f"{p.display.icon} {p.display.long_label}")
        for p in providers
    ]


def _split_provider_options_from_data(data: dict) -> dict:
    """Re-shape flat session data into R4.2 schema.

    Top-level keys listed in `_PROVIDER_OPTION_KEYS` are moved into a
    `provider_options` sub-dict. Other keys (name, project_dir, color,
    prompt, ...) stay where they are. If `provider_options` already
    exists in `data`, the moved keys are merged in (existing entries
    preserved, conflicts resolved in favor of the just-moved value).
    """
    result = dict(data)
    opts = dict(result.get("provider_options") or {})
    for key in _PROVIDER_OPTION_KEYS:
        if key in result:
            opts[key] = result.pop(key)
    if opts:
        result["provider_options"] = opts
    return result


def _flatten_session_for_legacy_dialog(session: Optional[dict]) -> Optional[dict]:
    """Unfold `provider_options` back onto top-level so the legacy
    ClaudeCodeDialog __init__ (which reads top-level keys) populates
    its checkboxes/entries unchanged.

    Returns None when session is None (Add-mode), else a NEW dict
    leaving the original untouched.
    """
    if session is None:
        return None
    if "provider_options" not in session:
        return dict(session)
    flat = dict(session)
    opts = flat.pop("provider_options", {}) or {}
    # provider_options wins over a (rare) duplicate top-level key
    flat.update(opts)
    return flat


class AISessionDialog(ClaudeCodeDialog):
    """Provider-aware session dialog (T2.6).

    T2.6 changes vs T2.5:
      - Hides parent's hardcoded Claude checkboxes (chk_sudo /
        chk_resume / chk_skip_perms) — schema-driven widgets are the
        sole source of provider-specific values.
      - Renders widgets from `provider.get_dialog_schema()` into a
        dedicated container.
      - Re-renders schema when the user changes the provider dropdown.
      - get_data() reads schema-driven widgets, drops parent's static
        provider keys, and emits canonical R4.2 schema.
    """

    def __init__(self, parent, session=None):
        # Lazy import — avoids touching providers package when GTK
        # tests strip-mock the module.
        from bterminal.providers import get_registry

        self._registry = get_registry()
        self._provider_combo_items = _build_provider_combo_items(self._registry)

        if session and session.get("provider"):
            initial_provider = session["provider"]
        else:
            initial_provider = self._registry.default_provider().name
        self._initial_provider_name = initial_provider
        # Keep the original session dict (post-flatten) for re-rendering
        # schema when the user switches provider.
        self._session_data: dict = _flatten_session_for_legacy_dialog(session) or {}

        # Unfold provider_options so the legacy dialog's __init__ can
        # populate its existing checkboxes from top-level keys.
        super().__init__(parent, self._session_data or None)

        self.set_title("Edit AI Session" if session else "Add AI Session")

        # State for schema-driven widgets (rebuilt on provider change).
        self._schema_widgets: dict[str, dict] = {}
        self._schema_container: Optional[Gtk.Box] = None

        self._hide_legacy_provider_widgets()
        self._inject_provider_dropdown()
        self._inject_schema_container()
        self._render_schema_for_current_provider()

    # ─── UI construction ────────────────────────────────────────────────────

    def _hide_legacy_provider_widgets(self):
        """Make parent's hardcoded Claude checkboxes invisible — they're
        replaced by schema-driven widgets. set_no_show_all keeps them
        hidden through subsequent show_all() calls."""
        for w in (self.chk_sudo, self.chk_resume, self.chk_skip_perms):
            w.set_no_show_all(True)
            w.set_visible(False)

    def _inject_provider_dropdown(self):
        """Insert a "AI Provider:" combo at the top of the content area."""
        box = self.get_content_area()

        provider_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        provider_grid.set_margin_bottom(4)

        lbl = Gtk.Label(label="AI Provider:", halign=Gtk.Align.END)
        provider_grid.attach(lbl, 0, 0, 1, 1)

        self.provider_combo = Gtk.ComboBoxText()
        self.provider_combo.set_hexpand(True)
        active_idx = 0
        for i, (name, label) in enumerate(self._provider_combo_items):
            self.provider_combo.append_text(label)
            if name == self._initial_provider_name:
                active_idx = i
        self.provider_combo.set_active(active_idx)
        # Re-render schema-driven fields when the user picks a new provider.
        self.provider_combo.connect("changed", self._on_provider_changed)
        provider_grid.attach(self.provider_combo, 1, 0, 1, 1)

        provider_grid.show_all()
        box.pack_start(provider_grid, False, False, 0)
        box.reorder_child(provider_grid, 0)

    def _inject_schema_container(self):
        """Create the empty container that holds provider-specific
        widgets — populated/replaced by _render_schema_for_current_provider."""
        box = self.get_content_area()
        self._schema_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4,
        )
        self._schema_container.show()
        # Pack at end (after all parent widgets) and reorder right
        # below the dropdown grid, so the schema fields visually
        # belong with the provider selection.
        box.pack_start(self._schema_container, False, False, 0)
        box.reorder_child(self._schema_container, 1)

    def _on_provider_changed(self, _combo):
        """Provider dropdown changed → wipe + rebuild schema widgets."""
        self._render_schema_for_current_provider()

    def _render_schema_for_current_provider(self):
        """Clear schema container + re-render widgets for current provider.

        Initial values come from the session being edited (if any) —
        flat keys after _flatten_session_for_legacy_dialog. Switching
        provider preserves the user's current widget values across all
        keys, including those NOT in the new schema — see task #59
        (2026-05-07). Without that preservation, edits to Claude-only
        fields (resume, sudo) made before a switch to Copilot would
        silently revert to constructor defaults on a return switch.
        """
        if self._schema_container is None:
            return

        # Snapshot widget values BEFORE clearing; merge them into the
        # canonical _session_data so they survive even when the next
        # schema doesn't expose them (task #59 fix). Without this merge
        # `prior_values` was a local that got dropped on function
        # return, losing user edits across a roundtrip switch.
        prior_values = self._collect_current_schema_values()
        self._session_data.update(prior_values)

        for child in self._schema_container.get_children():
            self._schema_container.remove(child)
        self._schema_widgets.clear()

        provider_name = self.get_active_provider_name()
        provider = self._registry.get(provider_name)
        schema = provider.get_dialog_schema()

        # _session_data already absorbed prior widget values — single
        # source of truth for initial values.
        initial = dict(self._session_data)

        for entry in schema:
            self._add_schema_field(entry, initial)

        self._schema_container.show_all()

    def _add_schema_field(self, entry: tuple, initial: dict):
        """Build a single widget for a schema entry and pack it into
        the schema container. Records the widget under self._schema_widgets
        so get_data() can read it back."""
        if len(entry) < 3:
            return
        key, widget_type, label = entry[0], entry[1], entry[2]
        extras = entry[3:] if len(entry) > 3 else ()

        if widget_type == "checkbox":
            widget = Gtk.CheckButton(label=label)
            widget.set_active(bool(initial.get(key, False)))
            self._schema_container.pack_start(widget, False, False, 0)
            self._schema_widgets[key] = {"widget": widget, "type": "checkbox"}

        elif widget_type == "combo":
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(
                Gtk.Label(label=label, halign=Gtk.Align.END), False, False, 0,
            )
            combo = Gtk.ComboBoxText()
            options = list(extras[0]) if extras else []
            for opt in options:
                combo.append_text(opt)
            current = initial.get(key)
            if current in options:
                combo.set_active(options.index(current))
            elif options:
                combo.set_active(0)
            row.pack_start(combo, True, True, 0)
            self._schema_container.pack_start(row, False, False, 0)
            self._schema_widgets[key] = {
                "widget": combo, "type": "combo", "options": options,
            }

        elif widget_type == "text":
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(
                Gtk.Label(label=label, halign=Gtk.Align.END), False, False, 0,
            )
            entry_widget = Gtk.Entry()
            entry_widget.set_hexpand(True)
            entry_widget.set_text(str(initial.get(key, "")))
            # Task #71 (2026-05-07): extras[0] (when present) is the
            # placeholder shown in the empty Entry — used to display
            # the provider's default template so the user can copy/
            # paste/edit it instead of starting from scratch.
            placeholder = extras[0] if extras else ""
            if placeholder:
                entry_widget.set_placeholder_text(str(placeholder))
            row.pack_start(entry_widget, True, True, 0)
            self._schema_container.pack_start(row, False, False, 0)
            self._schema_widgets[key] = {"widget": entry_widget, "type": "text"}

        elif widget_type == "textarea":
            # Multi-line input — used by T4.3 Copilot allowed_tools.
            # extras[0] (when present) is a placeholder/helptext shown
            # in dim above the field.
            placeholder = extras[0] if extras else ""
            label_widget = Gtk.Label(label=label, halign=Gtk.Align.START)
            self._schema_container.pack_start(label_widget, False, False, 0)
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC,
            )
            scrolled.set_min_content_height(80)
            text_view = Gtk.TextView()
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_view.set_monospace(True)
            buf = text_view.get_buffer()
            initial_text = str(initial.get(key, "") or "")
            if initial_text:
                buf.set_text(initial_text)
            scrolled.add(text_view)
            self._schema_container.pack_start(scrolled, False, False, 0)
            if placeholder:
                hint = Gtk.Label(
                    label=placeholder, halign=Gtk.Align.START,
                    xalign=0, wrap=True, max_width_chars=60,
                )
                hint.get_style_context().add_class("dim-label")
                self._schema_container.pack_start(hint, False, False, 0)
            self._schema_widgets[key] = {
                "widget": text_view, "type": "textarea",
                "buffer": buf,
            }

    def _collect_current_schema_values(self) -> dict:
        """Read current values from rendered schema widgets, returning
        a flat dict keyed by config_key. Used before re-rendering so
        the user doesn't lose their selections on provider switch."""
        out: dict = {}
        for key, info in self._schema_widgets.items():
            widget = info["widget"]
            wtype = info["type"]
            if wtype == "checkbox":
                out[key] = widget.get_active()
            elif wtype == "combo":
                idx = widget.get_active()
                options = info.get("options") or []
                if 0 <= idx < len(options):
                    out[key] = options[idx]
            elif wtype == "text":
                out[key] = widget.get_text().strip()
            elif wtype == "textarea":
                buf = info.get("buffer") or widget.get_buffer()
                start, end = buf.get_bounds()
                text = buf.get_text(start, end, False)
                out[key] = text.strip()
        return out

    def get_active_provider_name(self) -> str:
        """Provider name selected in the dropdown — fallback to
        initial value if the combo somehow has no active row."""
        idx = self.provider_combo.get_active()
        if idx < 0 or idx >= len(self._provider_combo_items):
            return self._initial_provider_name
        return self._provider_combo_items[idx][0]

    def get_data(self) -> dict:
        """R4.2-shaped session data: provider field + provider_options
        sub-dict carrying schema-driven flags. Parent's static checkbox
        values are intentionally dropped — schema is the source of truth.
        """
        base = super().get_data()
        base["provider"] = self.get_active_provider_name()

        # Drop parent's static provider-specific values (chk_sudo /
        # chk_resume / chk_skip_perms still emit them). Schema-driven
        # widgets replace them below.
        for key in _PROVIDER_OPTION_KEYS:
            base.pop(key, None)

        # Read from schema-driven widgets — current provider's truth.
        base.update(self._collect_current_schema_values())

        return _split_provider_options_from_data(base)


__all__ = [
    "AISessionDialog",
    "_build_provider_combo_items",
    "_flatten_session_for_legacy_dialog",
    "_split_provider_options_from_data",
    "is_valid_allowed_tool_rule",
    "parse_allowed_tools_text",
]
