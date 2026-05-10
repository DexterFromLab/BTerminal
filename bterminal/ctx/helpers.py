"""Ctx subsystem utilities — project name resolution, registration checks,
and wizard launcher. Depends on `ctx` CLI being installed in PATH.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ctx/detect.py` (and parts into `bterminal/ctx/wizard.py`)
in a later migration etap.
"""

import os
import shutil
import sqlite3
import subprocess

from bterminal.config import CTX_DB


def ensure_context_file_alongside_claude(project_dir, filename) -> str:
    """Ensure <filename> exists next to CLAUDE.md, mirroring its content.

    Generalized at #92: each AI provider declares its context filename
    via `capabilities.context_file` (Copilot=AGENTS.md, Aider=AIDER.md,
    future providers add their own). BTerminal generates the canonical
    context into CLAUDE.md and mirrors it as that filename so all
    providers see the same content without the user maintaining N copies.

    Order of attempts:
      1. If <filename> is the same as CLAUDE.md (provider's own file),
         return "self" — Claude doesn't need a mirror of itself.
      2. If <filename> already exists as a regular file, do nothing —
         the user may have customized it.
      3. If <filename> exists as a symlink and points (literally) to
         "CLAUDE.md", but CLAUDE.md doesn't exist yet, treat the link
         as 'broken-but-intentional': leave it for now (caller will
         retry once CLAUDE.md is generated).
      4. If <filename> exists as a symlink to ANYTHING ELSE (a stale
         link from a renamed source), and CLAUDE.md exists, replace
         it with a fresh "CLAUDE.md" symlink — #92's "broken symlink
         fixed" requirement.
      5. If CLAUDE.md doesn't exist at all, return "no_source".
      6. Try os.symlink("CLAUDE.md", <filename>) with a RELATIVE target
         so the link survives moving project_dir around.
      7. If symlink raises (cross-FS, FAT/exFAT mount, restricted
         permissions), fall back to a regular file copy.

    Returns:
      "self"        — filename matches CLAUDE.md (no mirror needed).
      "exists"      — file/link was already there; left untouched.
      "fixed"       — replaced a broken symlink with a fresh one.
      "symlink"     — newly created symbolic link.
      "copy"        — fallback regular-file copy.
      "no_source"   — CLAUDE.md missing; nothing to mirror.
      "failed"      — both symlink and copy raised; non-fatal.
    """
    project_dir = os.fspath(project_dir)
    if not filename or filename == "CLAUDE.md":
        return "self"

    claude_md = os.path.join(project_dir, "CLAUDE.md")
    target = os.path.join(project_dir, filename)

    if os.path.lexists(target):
        # Path 4: stale-symlink repair. Only intervene when (a) it's a
        # symlink, (b) the target it points to ISN'T literally
        # "CLAUDE.md" (or doesn't resolve), AND (c) CLAUDE.md exists
        # so we can repoint it. Anything else (regular file, existing
        # working symlink) is left untouched.
        if os.path.islink(target) and os.path.exists(claude_md):
            try:
                link_target = os.readlink(target)
            except OSError:
                link_target = None
            link_resolves = os.path.exists(target)
            if link_target != "CLAUDE.md" and not link_resolves:
                # Broken stale link — repoint at CLAUDE.md
                try:
                    os.unlink(target)
                    os.symlink("CLAUDE.md", target)
                    return "fixed"
                except OSError:
                    return "failed"
        return "exists"

    if not os.path.exists(claude_md):
        return "no_source"

    try:
        os.symlink("CLAUDE.md", target)
        return "symlink"
    except OSError:
        pass

    try:
        shutil.copy(claude_md, target)
        return "copy"
    except OSError:
        return "failed"


def ensure_agents_md_alongside_claude(project_dir) -> str:
    """Backward-compat shim for callers that pre-date #92's
    generalization. Delegates to ensure_context_file_alongside_claude
    with the AGENTS.md filename Copilot expects."""
    return ensure_context_file_alongside_claude(project_dir, "AGENTS.md")


def _render_claude_md(project_name: str) -> str:
    """Default CLAUDE.md content for a freshly-bootstrapped project.

    Single source of truth used by both the GUI ctx wizard
    (CtxSetupWizard._execute) and the REST add_ai handler so UI and
    REST sessions get identical context scaffolding.
    """
    return (
        f"# {project_name}\n\n"
        f"Context is loaded automatically via intro prompt. "
        f"No need to run `ctx get` manually.\n\n"
        f"During work:\n"
        f"- Save important discoveries: `ctx set {project_name} <key> <value>`\n"
        f"- Append to existing: `ctx append {project_name} <key> <value>`\n"
        f'- Before ending session: `ctx summary {project_name} "<what was done>"`\n'
        f"\n"
        f"## Consult & Tribunal (CLI tools)\n\n"
        f'Consult external AI models: `consult "question"`\n'
        f'Specific model: `consult -m <model_id> "question"` '
        f"— ALWAYS check available models first: `consult models`\n"
        f"Model names are FULL IDs with provider prefix, "
        f"e.g. `google/gemini-2.5-pro`, `openai/gpt-5-codex`, "
        f"`deepseek/deepseek-r1` — DO NOT abbreviate.\n"
        f'Attach a file as context: `consult -f file.py "question"`\n'
        f'Tribunal — multi-model AI debate: `consult debate "problem"`\n'
        f'  File context: `consult debate -f file.py "problem"`\n'
        f"  Default roles: `--analyst claude-code/opus "
        f"--arbiter claude-code/opus`\n"
        f"  Pick Advocate and Critic as needed from: "
        f"`openai/gpt-5-codex`, `deepseek/deepseek-r1`, "
        f"`google/gemini-2.5-pro`\n"
        f'  Example: `consult debate "problem" '
        f"--analyst claude-code/opus --advocate openai/gpt-5-codex "
        f"--critic deepseek/deepseek-r1 --arbiter claude-code/opus`\n"
        f"\n"
        f"## Task management (CLI tool)\n\n"
        f"IMPORTANT: Use the `tasks` CLI tool via Bash — NOT the "
        f"built-in TaskCreate/TaskUpdate/TaskList tools.\n"
        f"The built-in task tools are a different system. "
        f"Always use `tasks` in Bash.\n\n"
        f"```bash\n"
        f"tasks list {project_name}                           # show all tasks\n"
        f"tasks context {project_name}                        # show tasks + next task instructions\n"
        f'tasks add {project_name} "description"              # add a task\n'
        f"tasks done {project_name} <task_id>                 # mark task as done\n"
        f"tasks --help                                # full help\n"
        f"```\n\n"
        f"Do NOT pick up tasks on your own. Only execute tasks "
        f"when the auto-trigger system sends you a command.\n"
    )


def bootstrap_provider_context_files(
    project_dir, project_name: str = "",
) -> dict:
    """Headless equivalent of CtxSetupWizard's CLAUDE.md generation +
    per-provider mirroring. Used by REST `POST /api/sessions/ai` (#113)
    so test-driven session creation produces the same on-disk artefacts
    as the GUI wizard does.

    Steps:
      1. Resolve project_name from project_dir basename if not provided.
      2. Write CLAUDE.md with the default template iff it doesn't exist.
      3. Mirror to every registered provider's context_file (AGENTS.md,
         AIDER.md, …) via ensure_context_files_for_all_providers.

    Skips ctx-DB registration on purpose — that requires user-supplied
    description/key/value which the GUI wizard collects but REST cannot.
    The user can register later via `ctx init` or by re-opening the
    sidebar Edit dialog (which triggers the full wizard).

    Returns the mirror result dict (filename → status), or {} on no-op
    (invalid path) / failure.
    """
    if not project_dir:
        return {}
    project_dir = os.fspath(project_dir)
    if not os.path.isdir(project_dir):
        return {}
    if not project_name:
        project_name = _smart_project_name(project_dir) or os.path.basename(
            project_dir.rstrip("/")
        )
    claude_md = os.path.join(project_dir, "CLAUDE.md")
    if not os.path.exists(claude_md):
        try:
            with open(claude_md, "w") as f:
                f.write(_render_claude_md(project_name))
        except OSError:
            return {}
    return ensure_context_files_for_all_providers(project_dir)


def ensure_context_files_for_all_providers(project_dir) -> dict:
    """Walk the provider registry and run the mirror logic for each
    provider that declares a `capabilities.context_file`. Returns a
    dict mapping filename → result code so callers can log per-provider
    outcomes.

    Used by ctx wizard finalize: instead of hardcoding AGENTS.md, the
    wizard now ensures every registered provider's context file exists.
    """
    from bterminal.providers import get_registry

    registry = get_registry()
    out: dict = {}
    for name in registry.names():
        try:
            prov = registry.get(name)
        except (KeyError, AttributeError):
            continue
        ctx_file = prov.capabilities.context_file
        if not ctx_file:
            continue
        out[ctx_file] = ensure_context_file_alongside_claude(
            project_dir, ctx_file)
    return out


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
    walk up to the nearest git root and use that name instead. The
    walk-up heuristic only kicks in WITH a .git anchor — without one
    we trust the basename, because directories like ~/Dokumenty/test
    or ~/Desktop/scratch are standalone projects whose parent is just
    a home-directory bucket, not a meaningful project root.

    Bug #56 (2026-05-07): pre-fix, generic basenames without a git
    anchor fell through to the parent dir name, leading the ctx
    wizard to prefill 'Dokumenty' instead of 'test' for the user's
    ~/Dokumenty/test session.
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
    # No git root found — basename is the best signal we have. The
    # user explicitly picked this folder; the fact its name happens
    # to match a generic subdir keyword doesn't mean its parent is
    # the project (that gave us 'Dokumenty' / 'Desktop' garbage).
    return basename


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
        # T3.1: ClaudeStatsReader uses _cached_path; pre-T3 _SessionStatsReader
        # used _cached. Try the new name first, fall back for any legacy reader.
        jsonl_path = (
            getattr(stats_bar._reader, "_cached_path", None)
            or getattr(stats_bar._reader, "_cached", None)
        )
    cmd = ["claude_log", "collect", project_dir]
    if jsonl_path:
        cmd.append(jsonl_path)
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass
