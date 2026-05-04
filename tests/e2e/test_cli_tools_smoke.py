"""Smoke testy dla CLI tools (`tools/{ctx, tasks, consult, memory_wizard, claude_log}`).

Każdy CLI musi:
  1. Odpowiadać na `--help` / pomoc bez crashu
  2. Robić basic CRUD round-trip na izolowanym tmp HOME (żeby nie tknąć
     prawdziwego `~/.claude-context/` ani `~/.config/bterminal/`)

Catches:
  - Refactor regresje w CLI (np. import path break, Path() argumenty)
  - Schema drift między CLI a tym jak BTerminal czyta/zapisuje DB
  - Behavior changes np. ctx get format
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
TOOLS = REPO_ROOT / "tools"


# ─── helpers ─────────────────────────────────────────────────────────────────

def run_cli(tool_name, *args, env=None, timeout=10):
    """Run a CLI tool from tools/ with isolated env. Returns CompletedProcess."""
    bin_path = TOOLS / tool_name
    if not bin_path.exists():
        pytest.skip(f"CLI tool not found: {bin_path}")
    full_env = {**os.environ}
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(bin_path), *args],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Tmp HOME — każdy test izoluje swoje DB / config / claude-context."""
    home = tmp_path / "home"
    home.mkdir()
    return home


# ─── ctx ─────────────────────────────────────────────────────────────────────

def test_ctx_help_works():
    """`ctx --help` exit 0 + nazwa narzędzia w outpucie."""
    r = run_cli("ctx", "--help")
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "ctx" in r.stdout.lower() or "ctx" in r.stderr.lower()


def test_ctx_init_set_get_roundtrip(isolated_home):
    """ctx init <project> → ctx set X "value" → ctx get X
    → "value" w outpucie. Smoke pełnego cyklu DB."""
    env = {"HOME": str(isolated_home)}
    project = "smoke_test_ctx"

    r1 = run_cli("ctx", "init", project, "smoke test description", env=env)
    assert r1.returncode == 0, f"init failed: {r1.stderr}"

    r2 = run_cli("ctx", "set", project, "test_key", "smoke_value_42", env=env)
    assert r2.returncode == 0, f"set failed: {r2.stderr}"

    r3 = run_cli("ctx", "get", project, env=env)
    assert r3.returncode == 0, f"get failed: {r3.stderr}"
    assert "smoke_value_42" in r3.stdout, (
        f"value not found in get output:\n{r3.stdout}"
    )


def test_ctx_set_overwrites_value(isolated_home):
    """Drugi `ctx set` na ten sam klucz nadpisuje wartość."""
    env = {"HOME": str(isolated_home)}
    project = "overwrite_test"
    run_cli("ctx", "init", project, "smoke test description", env=env)
    run_cli("ctx", "set", project, "k", "first_value", env=env)
    run_cli("ctx", "set", project, "k", "second_value", env=env)
    r = run_cli("ctx", "get", project, env=env)
    assert "second_value" in r.stdout
    assert "first_value" not in r.stdout


def test_ctx_db_created_at_expected_path(isolated_home):
    """Po `ctx init` DB istnieje w `~/.claude-context/context.db`."""
    env = {"HOME": str(isolated_home)}
    run_cli("ctx", "init", "path_test", "smoke test description", env=env)
    db = isolated_home / ".claude-context" / "context.db"
    assert db.exists(), f"DB not created at {db}"
    # Schema check — sessions table + ctx_entries (lub similar)
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    assert "sessions" in tables, f"sessions table missing — got {tables}"


# ─── tasks ───────────────────────────────────────────────────────────────────

def test_tasks_help_works():
    r = run_cli("tasks", "--help")
    assert r.returncode == 0
    assert "tasks" in (r.stdout + r.stderr).lower()


def test_tasks_add_list_done_roundtrip(isolated_home):
    """tasks add <p> "desc" → tasks list <p> → done → list pokazuje [✓].

    Wymaga ctx init najpierw bo tasks używają ctx DB."""
    env = {"HOME": str(isolated_home)}
    project = "task_smoke"

    # tasks/ctx share DB — init przez ctx (lub tasks samo init'uje? — TBD)
    run_cli("ctx", "init", project, "smoke test description", env=env)

    r1 = run_cli("tasks", "add", project, "test task description", env=env)
    assert r1.returncode == 0, f"add failed: {r1.stderr}"

    r2 = run_cli("tasks", "list", project, env=env)
    assert r2.returncode == 0
    assert "test task description" in r2.stdout

    # Get task ID — assume "1" jako pierwszy auto-numerowany
    r3 = run_cli("tasks", "done", project, "1", env=env)
    assert r3.returncode == 0, f"done failed: {r3.stderr}"

    r4 = run_cli("tasks", "list", project, env=env)
    # ASCII checkbox na "done" tasks
    assert "✓" in r4.stdout or "done" in r4.stdout.lower()


def test_tasks_pending_count(isolated_home):
    """`tasks pending <p>` zwraca liczbę open tasków."""
    env = {"HOME": str(isolated_home)}
    project = "pending_test"
    run_cli("ctx", "init", project, "smoke test description", env=env)
    run_cli("tasks", "add", project, "task one", env=env)
    run_cli("tasks", "add", project, "task two", env=env)
    run_cli("tasks", "add", project, "task three", env=env)

    r = run_cli("tasks", "pending", project, env=env)
    assert r.returncode == 0
    assert "3" in r.stdout, f"expected 3 pending, got: {r.stdout!r}"


# ─── consult ─────────────────────────────────────────────────────────────────

def test_consult_help_works():
    r = run_cli("consult", "--help")
    assert r.returncode == 0
    assert "consult" in (r.stdout + r.stderr).lower()


def test_consult_models_listing(isolated_home):
    """`consult models` listuje skonfigurowane modele bez wymagania
    API key (czytanie configu lokalnego)."""
    env = {"HOME": str(isolated_home)}
    r = run_cli("consult", "models", env=env)
    assert r.returncode == 0, f"models failed: {r.stderr}"
    # Default config zawiera google/gemini-2.5-pro + openai/gpt-4o
    output = r.stdout + r.stderr
    has_default = "gemini" in output.lower() or "gpt-4o" in output.lower()
    assert has_default, f"no default models listed:\n{output[:500]}"


# ─── claude_log ──────────────────────────────────────────────────────────────

def test_claude_log_help_works():
    r = run_cli("claude_log", "--help")
    # Niektóre CLI nie mają --help — akceptuj exit 1 jeśli widać help text
    output = r.stdout + r.stderr
    assert "claude_log" in output.lower() or "usage" in output.lower(), (
        f"no help-like output:\n{output[:200]}"
    )


def test_claude_log_collect_with_missing_dir(isolated_home):
    """collect na nieistniejący project_dir → graceful (no crash)."""
    env = {"HOME": str(isolated_home)}
    r = run_cli(
        "claude_log", "collect",
        str(isolated_home / "nonexistent"),
        env=env, timeout=5,
    )
    # Może exit 0 (no-op) albo 1 (error message), ale NIE traceback
    assert "Traceback" not in r.stderr, f"crashed: {r.stderr[:300]}"


# ─── memory_wizard ───────────────────────────────────────────────────────────

def test_memory_wizard_help_works():
    r = run_cli("memory_wizard", "--help")
    assert r.returncode == 0
    output = r.stdout + r.stderr
    assert "wizard" in output.lower() or "memory" in output.lower() or "rules" in output.lower(), (
        f"no help-like output:\n{output[:300]}"
    )
