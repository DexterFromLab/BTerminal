"""BTerminal misc helpers — Claude path, intro prompt builder, ctx I/O,
clipboard + image attachments, project description detector.

This module collects helpers that lived in bterminal.py before the
modular refactor. They're not yet split into themed modules because
they're already tightly coupled (clipboard ↔ images ↔ ctx etc.) — a
future etap may further separate them.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/` package in a later migration etap.
"""

import glob
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from bterminal.config import (
    APP_VERSION,
    CATPPUCCIN,
    CONFIG_DIR,
    CTX_DB,
    CTX_IMAGES_DIR,
    REPO_DIR,
    _OPTIONS,
)
from bterminal.ctx.helpers import _resolve_ctx_project_name, _smart_project_name
from bterminal.sidecar_runtime import SidecarManifest


_GLOBAL_RULES_FILE = Path(__file__).parent.parent / "defaults" / "global_rules.txt"
_BUNDLED_SKILLS_DIR = Path(__file__).parent.parent / "defaults" / "skills"

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".ico"}


def _list_available_plugins(app) -> list[dict]:
    """Unified view of GTK plugins + sidecar manifests for per-tab UI
    (ClaudeCodeDialog checkbox list, /api endpoints, intro-prompt builder).

    Each entry: {name, title, type:'gtk'|'sidecar', default_in_session,
    currently_enabled_globally}.

    For GTK plugins, currently_enabled_globally reflects whether the plugin
    is currently loaded in app._plugins. For sidecars, manifest existence is
    enough — they are always available to be selected per tab (start happens
    on demand via refcount).
    """
    out: list[dict] = []
    for name, plugin in app._plugins.items():
        out.append({
            "name": name,
            "title": getattr(plugin, "title", "") or name,
            "type": "gtk",
            "default_in_session": getattr(plugin, "default_in_session", True),
            "currently_enabled_globally": True,
        })
    for name, manifest in app.sidecar_manifests.items():
        out.append({
            "name": name,
            "title": manifest.title or name,
            "type": "sidecar",
            "default_in_session": manifest.default_in_session,
            "currently_enabled_globally": True,
        })
    out.sort(key=lambda d: (d["type"], d["name"]))
    return out


def _build_sidecar_intro_section(manifest: SidecarManifest) -> str:
    """Format a sidecar manifest's prompt for injection into the Claude Code
    intro prompt. Mirrors the style of GTK plugin .get_session_context() —
    just text, no surrounding scaffolding.

    If the manifest's prompt already starts with a markdown header (## ...)
    we pass it through; otherwise we prefix one built from manifest.title.
    """
    title = manifest.title or manifest.name
    prompt = (manifest.prompt or "").strip()
    if not prompt:
        return f"## {title}\n(no prompt configured)"
    if prompt.lstrip().startswith("#"):
        return prompt
    return f"## {title}\n\n{prompt}"


def _compute_intro_prompt_for_tab(app, tab) -> str:
    """Pure-function variant of the intro-prompt builder used at
    start_claude_session. Returns the string that would be injected if
    Claude Code were spawned in this tab right now. No side effects —
    safe to call from REST endpoints.
    """
    # Lazy import: _build_intro_prompt jest w ui/dialogs/claude_code.py,
    # który ładuje GTK + szereg zależności. Tu robimy lazy żeby zerwać
    # cyrkular import (helpers ↔ ui.dialogs).
    from bterminal.ui.dialogs.claude_code import _build_intro_prompt

    config = getattr(tab, "claude_config", None) or {}
    custom_prompt = config.get("prompt", "")
    project_dir = config.get("project_dir", "")
    if project_dir:
        project_name = _resolve_ctx_project_name(project_dir)
        prompt = _build_intro_prompt(project_name)
        if custom_prompt:
            prompt += "\n\n" + custom_prompt
    else:
        prompt = custom_prompt

    enabled = getattr(tab, "enabled_plugins", None)

    # GTK plugins
    for plugin in app._plugins.values():
        if enabled is not None and plugin.name not in enabled:
            continue
        try:
            ctx = plugin.get_session_context()
            if ctx:
                prompt = (prompt + "\n\n" + ctx) if prompt else ctx
        except Exception:
            pass

    # Sidecars
    if enabled is not None:
        sidecar_names = set(enabled) & set(app.sidecar_manifests)
    else:
        sidecar_names = {
            n for n, m in app.sidecar_manifests.items() if m.default_in_session
        }
    for name in sorted(sidecar_names):
        manifest = app.sidecar_manifests[name]
        try:
            section = _build_sidecar_intro_section(manifest)
            if section:
                prompt = (prompt + "\n\n" + section) if prompt else section
        except Exception:
            pass
    return prompt



def _find_claude_path():
    """Locate Claude Code binary across common install locations.

    Returns absolute path if found, otherwise None. Handles npm-global
    (default prefix used by our installer), nvm, system paths, and macOS
    homebrew. Falls back to PATH lookup with an extended search so that
    GUI launches (which often miss ~/.npm-global/bin from ~/.bashrc)
    still resolve the binary.
    """
    candidates = [
        os.path.expanduser("~/.local/bin/claude"),
        os.path.expanduser("~/.npm-global/bin/claude"),
        "/usr/local/bin/claude",
        "/usr/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    candidates += sorted(
        glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/claude")),
        reverse=True,
    )
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    extra = os.pathsep.join([
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/.local/bin"),
    ])
    env_path = os.environ.get("PATH", "") + os.pathsep + extra
    return shutil.which("claude", path=env_path)

CLAUDE_PATH = _find_claude_path()


def _claude_log_dir(project_dir):
    """Per-project Claude log dir, with safe fallback to `.claude_log/`.

    Why: the BTerminal repo itself ships an executable named `claude_log`
    in its root, which collides with the default `<project_dir>/claude_log/`
    directory and breaks `Path.mkdir(exist_ok=True)`. The hidden fallback
    keeps every other project on the original path.
    """
    base = Path(project_dir)
    primary = base / "claude_log"
    if primary.exists() and not primary.is_dir():
        return base / ".claude_log"
    return primary

def _create_color_combo():
    """Create a ComboBox with color swatches for SESSION_COLORS."""
    store = Gtk.ListStore(str, GdkPixbuf.Pixbuf)
    for hex_color in SESSION_COLORS:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 20, 14)
        pixbuf.fill((r << 24) | (g << 16) | (b << 8) | 0xFF)
        store.append([hex_color, pixbuf])
    combo = Gtk.ComboBox(model=store, hexpand=True)
    renderer_pixbuf = Gtk.CellRendererPixbuf()
    combo.pack_start(renderer_pixbuf, False)
    combo.add_attribute(renderer_pixbuf, "pixbuf", 1)
    renderer_text = Gtk.CellRendererText()
    combo.pack_start(renderer_text, True)
    combo.add_attribute(renderer_text, "text", 0)
    combo.set_active(0)
    return combo


# ─── SessionManager / ConsultManager ──────────────────────────────────────────





# ─── ClaudeCodeDialog ─────────────────────────────────────────────────────────


def _fetch_ctx_output(project_name):
    """Run 'ctx get <project>' and return its stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["ctx", "get", project_name, "--shared"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""




def _read_global_rules() -> list:
    """Read enabled rules from defaults/global_rules.txt (lines not starting with #)."""
    try:
        text = _GLOBAL_RULES_FILE.read_text(errors="replace")
        return [l.strip() for l in text.splitlines()
                if l.strip() and not l.strip().startswith("#")]
    except Exception:
        return []


def _fetch_rules_block(project_name):
    """Return formatted rules block for project, or empty string if none."""
    try:
        result = subprocess.run(
            ["ctx", "rules", "inject", project_name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _tools_help(project_name):
    """Return detailed tool instructions string for a given project.

    Covers ctx (context management), consult (external AI queries / Tribunal),
    and tasks (CLI task manager with auto-trigger).
    """
    return (
        f"Kontekst zarządzasz przez: ctx --help\n"
        f"Ważne odkrycia zapisuj: ctx set {project_name} <key> <value>\n"
        f"Dołączanie do istniejącego: ctx append {project_name} <key> <value>\n"
        f'Przed zakończeniem sesji: ctx summary {project_name} "<co zrobiono>"\n'
        f"\n"
        f"Konsultacje z zewnętrznymi modelami AI: consult \"pytanie\"\n"
        f"Konkretny model: consult -m <model_id> \"pytanie\" — ZAWSZE najpierw sprawdź dostępne modele: consult models\n"
        f"Nazwy modeli to PEŁNE ID z prefixem providera, np. 'google/gemini-2.5-pro', 'openai/gpt-5-codex', 'deepseek/deepseek-r1' — NIE skracaj.\n"
        f"Dołączanie pliku jako kontekst: consult -f plik.py \"pytanie\"\n"
        f"Tribunal — debata wielu modeli AI: consult debate \"problem\"\n"
        f"  Domyślne role: --analyst claude-code/opus --arbiter claude-code/opus\n"
        f"  Advocate i Critic dobieraj wg potrzeb spośród: openai/gpt-5-codex, deepseek/deepseek-r1, google/gemini-2.5-pro\n"
        f'  Przykład: consult debate "problem" --analyst claude-code/opus --advocate openai/gpt-5-codex --critic deepseek/deepseek-r1 --arbiter claude-code/opus\n'
        f"\n"
        f"Dostępne narzędzie 'tasks' — ZEWNĘTRZNY CLI tool uruchamiany w Bash (NIE wbudowany TaskCreate/TaskList).\n"
        f"NIE pobieraj ani nie wykonuj zadań z listy samodzielnie.\n"
        f"Jeśli system auto-trigger wyśle Ci polecenie z listą zadań — wtedy wykonuj.\n"
        f"Po każdym wykonanym zadaniu MUSISZ oznaczyć je jako done: tasks done {project_name} <task_id>\n"
        f"Pomoc: tasks --help\n"
        f"\n"
        f"Memory Wizard — konfiguracja reguł na podstawie logów sesji:\n"
        f"  Dry-run (przejrzyj propozycje, zastosuj ręcznie wybrane):\n"
        f"    memory_wizard {project_name} --project-dir <dir> --dry-run\n"
        f"  Interaktywny (potwierdź każdą propozycję):\n"
        f"    memory_wizard {project_name} --project-dir <dir>\n"
        f"  Uruchom gdy użytkownik poprawia Cię wielokrotnie w ten sam sposób,\n"
        f"  lub po dłuższej sesji aby utrwalić wzorce w regułach."
    )




# ─── CtxEditDialog ────────────────────────────────────────────────────────────


CTX_DB = os.path.join(os.path.expanduser("~"), ".claude-context", "context.db")
CTX_IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".claude-context", "images")


def _clipboard_has_image_or_path():
    """Check if clipboard has an image bitmap or a text path to an image file."""
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    if clipboard.wait_is_image_available():
        return True
    text = clipboard.wait_for_text()
    if text:
        path = text.strip().strip("'\"")
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS:
            return True
    return False


def _clipboard_get_image_or_path():
    """Return (pixbuf, None) or (None, file_path) or (None, None)."""
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    pixbuf = clipboard.wait_for_image()
    if pixbuf:
        return pixbuf, None
    text = clipboard.wait_for_text()
    if text:
        path = text.strip().strip("'\"")
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS:
            return None, path
    return None, None


def _ensure_images_table():
    """Create images table in ctx database if it doesn't exist."""
    if not os.path.exists(CTX_DB):
        return
    db = sqlite3.connect(CTX_DB)
    db.execute(
        "CREATE TABLE IF NOT EXISTS images ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  project TEXT NOT NULL,"
        "  filename TEXT NOT NULL,"
        "  original_name TEXT,"
        "  added_at TEXT DEFAULT (datetime('now')),"
        "  UNIQUE(project, filename)"
        ")"
    )
    db.commit()
    db.close()


def _save_ctx_image(project, source, original_name=None):
    """Save image to ctx. source: file path (str) or GdkPixbuf.Pixbuf."""
    _ensure_images_table()
    proj_dir = os.path.join(CTX_IMAGES_DIR, project)
    os.makedirs(proj_dir, exist_ok=True)

    if isinstance(source, GdkPixbuf.Pixbuf):
        ext = ".png"
        if not original_name:
            original_name = "clipboard.png"
        filename = f"{uuid.uuid4().hex[:12]}{ext}"
        dest = os.path.join(proj_dir, filename)
        source.savev(dest, "png", [], [])
    else:
        if not original_name:
            original_name = os.path.basename(source)
        ext = os.path.splitext(original_name)[1] or ".png"
        filename = f"{uuid.uuid4().hex[:12]}{ext}"
        dest = os.path.join(proj_dir, filename)
        shutil.copy2(source, dest)

    db = sqlite3.connect(CTX_DB)
    db.execute(
        "INSERT OR REPLACE INTO images (project, filename, original_name, added_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (project, filename, original_name),
    )
    db.commit()
    db.close()
    return filename


def _delete_ctx_image(project, filename):
    """Delete an image file and its database entry."""
    path = os.path.join(CTX_IMAGES_DIR, project, filename)
    if os.path.exists(path):
        os.remove(path)
    if os.path.exists(CTX_DB):
        db = sqlite3.connect(CTX_DB)
        db.execute(
            "DELETE FROM images WHERE project = ? AND filename = ?",
            (project, filename),
        )
        db.commit()
        db.close()


def _detect_project_description(project_dir):
    """Detect project description from README or directory name."""
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        readme_path = os.path.join(project_dir, name)
        if os.path.isfile(readme_path):
            try:
                with open(readme_path, "r") as f:
                    for line in f:
                        line = line.strip().lstrip("#").strip()
                        if line:
                            return line[:100]
            except (IOError, UnicodeDecodeError):
                pass
    return os.path.basename(project_dir.rstrip("/"))



# ─── Ctx helpers (extracted) ─────────────────────────────────────────────────





# ─── SessionStatsBar (extracted) ─────────────────────────────────────────────




# ─── TerminalTab (extracted) ─────────────────────────────────────────────────



# ─── SessionSidebar (extracted) ──────────────────────────────────────────────


# ─── Ctx Import / Export ──────────────────────────────────────────────────────







# ─── ConsultPanel ────────────────────────────────────────────────────────────


# ─── Git Panel ────────────────────────────────────────────────────────────────
