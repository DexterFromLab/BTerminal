"""Ctx subsystem utilities — project name resolution, registration checks,
and wizard launcher. Depends on `ctx` CLI being installed in PATH.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ctx/detect.py` (and parts into `bterminal/ctx/wizard.py`)
in a later migration etap.
"""

import os
import sqlite3
import subprocess

from bterminal.config import CTX_DB


# Directory basenames that look like generic subfolders (docs/src/lib/...)
# rather than project roots — _smart_project_name walks up past these.
_GENERIC_SUBDIRS = frozenset({
    "docs", "doc", "src", "source", "lib", "libs", "app", "apps",
    "frontend", "backend", "web", "api", "test", "tests", "spec",
    "scripts", "script", "code", "project", "workspace", "core",
})


def _smart_project_name(project_dir):
    """Return a meaningful project name for a directory.

    If the directory's basename looks like a generic subfolder (docs, src, …),
    walk up to the nearest git root and use that name instead.
    Falls back to the directory's own basename.
    """
    if not project_dir:
        return ""
    normalized = project_dir.rstrip("/")
    basename = os.path.basename(normalized)
    if basename.lower() not in _GENERIC_SUBDIRS:
        return basename
    # Walk up looking for .git to find the project root
    path = normalized
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            break
        if os.path.isdir(os.path.join(path, ".git")):
            return os.path.basename(path)
        path = parent
    # No git root found — use the immediate parent name if available
    parent_name = os.path.basename(os.path.dirname(normalized))
    return parent_name if parent_name else basename


def _resolve_ctx_project_name(project_dir):
    """Resolve ctx project name from a project directory path.

    First looks up the sessions table by work_dir (exact match, then parent
    directories). Falls back to _smart_project_name.
    """
    if not project_dir or not os.path.exists(CTX_DB):
        return _smart_project_name(project_dir) if project_dir else None
    normalized = project_dir.rstrip("/")
    try:
        db = sqlite3.connect(CTX_DB)
        # Walk up from project_dir: first exact match, then parent dirs
        path = normalized
        while True:
            row = db.execute(
                "SELECT name FROM sessions WHERE RTRIM(work_dir, '/') = ?",
                (path,),
            ).fetchone()
            if row:
                db.close()
                return row[0]
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        db.close()
    except sqlite3.Error:
        pass
    return _smart_project_name(normalized)


def _is_ctx_project_registered(project_name):
    """Check if a ctx project is already registered in the database."""
    if not os.path.exists(CTX_DB):
        return False
    try:
        db = sqlite3.connect(CTX_DB)
        row = db.execute(
            "SELECT 1 FROM sessions WHERE name = ?", (project_name,)
        ).fetchone()
        db.close()
        return row is not None
    except sqlite3.Error:
        return False


def _is_ctx_available():
    """Check if ctx command is available."""
    try:
        subprocess.run(["ctx", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_ctx_wizard_if_needed(parent, data):
    """Launch ctx wizard if project_dir is set but ctx not registered.
    Returns (potentially modified) data dict."""
    project_dir = data.get("project_dir", "")
    if not project_dir or not _is_ctx_available():
        return data
    project_name = _smart_project_name(project_dir)
    if _is_ctx_project_registered(project_name):
        return data
    # Lazy: CtxSetupWizard will move into ctx_wizard.py later this etap.
    from bterminal import CtxSetupWizard
    wizard = CtxSetupWizard(parent, project_dir)
    wizard.run_wizard()
    return data


def _collect_claude_log(tab):
    """Collect the Claude Code session JSONL into project's claude_log/
    directory on tab close (delegates to `claude_log` CLI tool)."""
    claude_config = getattr(tab, "claude_config", None)
    if not claude_config:
        return
    project_dir = claude_config.get("project_dir", "")
    if not project_dir or not os.path.isdir(project_dir):
        return
    stats_bar = getattr(tab, "_stats_bar", None)
    jsonl_path = None
    if stats_bar and getattr(stats_bar, "_reader", None):
        jsonl_path = stats_bar._reader._cached
    cmd = ["claude_log", "collect", project_dir]
    if jsonl_path:
        cmd.append(jsonl_path)
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass
