"""Pin tests for tools/aider_setup_wizard (BUG#21).

Strategy: invoke the wizard with --headless in a subprocess and mock the
external world via a fake `ollama` binary placed on PATH. We never hit
the real Ollama daemon, never download a model, and never touch the
user's real ~/.config/bterminal. HOME is redirected to a tmp_path so
options.json + sentinel land in the test sandbox.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WIZARD = REPO_ROOT / "tools" / "aider_setup_wizard"


# ─── Fake `ollama` binary ────────────────────────────────────────────────


def _make_fake_ollama(bin_dir: Path, *, list_models: list[str],
                     pull_rc: int = 0) -> Path:
    """Write a small bash stub that emulates `ollama list` and `ollama pull`.

    `ollama list`  → header + one row per `list_models` tag
    `ollama pull X` → echoes a fake progress line then exits with pull_rc
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"{tag:<28}abc{i:05d}        100 MB    1 hour ago"
                     for i, tag in enumerate(list_models))
    listing = ("NAME                  ID            SIZE     MODIFIED\n"
               + rows + ("\n" if rows else ""))
    # Use heredoc so quoting stays sane regardless of tag content
    script = f"""#!/bin/bash
case "$1" in
  list)
    cat <<'EOF'
{listing}EOF
    ;;
  pull)
    echo "pulling manifest"
    echo "pulling $2: 100%"
    exit {pull_rc}
    ;;
  serve)
    sleep 60
    ;;
  *)
    echo "fake-ollama: unknown subcommand $1" >&2
    exit 99
    ;;
esac
"""
    path = bin_dir / "ollama"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _run_wizard(tmp_path: Path, *args: str,
                list_models: list[str] | None = None,
                pull_rc: int = 0,
                inject_ollama: bool = True) -> subprocess.CompletedProcess:
    """Run the wizard in an isolated env. Returns CompletedProcess."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    if inject_ollama:
        _make_fake_ollama(
            bin_dir,
            list_models=list_models or [],
            pull_rc=pull_rc,
        )
    # Hermetic PATH: only our bin_dir + system /usr/bin (for nvidia-smi
    # probe etc.). Crucially omits /usr/local/bin where a real ollama
    # might live on the dev host / VM — otherwise `inject_ollama=False`
    # would still find the real binary and the test wouldn't exercise
    # the 'Ollama missing' branch.
    env["PATH"] = f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    # Stop urllib daemon-probe from hitting localhost:11434 hanging tests.
    # The wizard already treats URLError as 'no daemon' — guarantee that by
    # making sure nothing is listening; we pick the smallest "Confirm.ask
    # would block" exit path: --headless skips the prompt automatically.

    return subprocess.run(
        [sys.executable, str(WIZARD), *args],
        env=env, capture_output=True, text=True, timeout=30,
    )


# ─── Happy path: recommended model selected & persisted ──────────────────


def test_wizard_writes_options_json_with_openai_prefix(tmp_path):
    """Pin: --headless picks the recommended model. options.json must end
    up with `default_local_model_for_provider.aider` set to an
    `openai/<tag>` string — that's what AiderProvider.build_argv reads.
    Without the prefix litellm picks the wrong route."""
    result = _run_wizard(
        tmp_path, "--headless", "--no-pull",
        list_models=["qwen2.5-coder:7b"],  # pretend it's already installed
    )
    assert result.returncode == 0, result.stderr or result.stdout
    options = json.loads(
        (tmp_path / ".config" / "bterminal" / "options.json").read_text())
    chosen = options["default_local_model_for_provider"]["aider"]
    assert chosen.startswith("openai/")
    assert ":" in chosen  # has Ollama-style size tag


def test_wizard_sentinel_only_when_session_id_given(tmp_path):
    """Pin: --session-id writes the sentinel so BT's child-exited hook
    knows which aider tab to relaunch. Without --session-id (manual
    entry from Narzędzia menu), no sentinel — BT would otherwise
    relaunch the wrong session."""
    res_with = _run_wizard(
        tmp_path / "with_sid", "--headless", "--no-pull",
        "--session-id", "abc-123",
        list_models=["qwen2.5-coder:7b"],
    )
    assert res_with.returncode == 0, res_with.stderr
    sentinel = (tmp_path / "with_sid" / ".config" / "bterminal"
                / ".aider_wizard_done.json")
    assert sentinel.exists()
    data = json.loads(sentinel.read_text())
    assert data["session_id"] == "abc-123"
    assert data["model"].startswith("openai/")
    assert isinstance(data["ts"], int)

    res_without = _run_wizard(
        tmp_path / "without_sid", "--headless", "--no-pull",
        list_models=["qwen2.5-coder:7b"],
    )
    assert res_without.returncode == 0, res_without.stderr
    sentinel2 = (tmp_path / "without_sid" / ".config" / "bterminal"
                 / ".aider_wizard_done.json")
    assert not sentinel2.exists()


def test_wizard_already_installed_skips_pull(tmp_path):
    """Pin: recommended model already in `ollama list` → no `pull`
    invocation. Detected by inspecting wizard stdout for the
    'already pulled' message. We can't observe subprocess calls here
    so the user-visible message is the contract.

    BUG#25: wizard CLI strings are English (matches `tasks`, `consult`,
    `memory_wizard` convention; GTK dialogs in BT itself still gettext-
    translate based on BT.language)."""
    result = _run_wizard(
        tmp_path, "--headless",
        list_models=["qwen2.5-coder:7b"],
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "already pulled" in result.stdout.lower()


# ─── Error paths ─────────────────────────────────────────────────────────


def test_wizard_no_ollama_headless_returns_nonzero(tmp_path):
    """Pin: --headless cannot run `curl install.sh` (would need user
    consent) so when Ollama is absent the wizard must exit with a
    non-zero code rather than silently making it look like a success."""
    result = _run_wizard(
        tmp_path, "--headless", "--no-pull",
        inject_ollama=False,  # no fake ollama on PATH
    )
    assert result.returncode != 0
    options_path = (tmp_path / ".config" / "bterminal" / "options.json")
    assert not options_path.exists()


def test_prompt_custom_tag_builds_synthetic_model_entry():
    """Pin (BUG#26): custom-tag path produces a dict that the rest of
    the wizard flow can treat as a catalog entry. Key invariants:
    `tag` and `label` carry the raw user input, `url` derives from
    the base name (everything before ':'), and the numeric fields are
    zero (we have no hardware data on custom tags so don't fake it)."""
    import importlib.util
    from importlib.machinery import SourceFileLoader
    here = (Path(__file__).resolve().parent.parent
            / "tools" / "aider_setup_wizard")
    loader = SourceFileLoader("_aider_wizard", str(here))
    spec = importlib.util.spec_from_loader("_aider_wizard", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    class _FakePrompt:
        @staticmethod
        def ask(*_a, **_kw):
            return "llama3.3:70b-instruct-q2_K"

    real_prompt = mod.Prompt
    mod.Prompt = _FakePrompt
    try:
        out = mod.prompt_custom_tag(mod.Console(quiet=True))
    finally:
        mod.Prompt = real_prompt
    assert out is not None
    assert out["tag"] == "llama3.3:70b-instruct-q2_K"
    assert out["label"] == "llama3.3:70b-instruct-q2_K"
    assert out["url"] == "https://ollama.com/library/llama3.3"
    assert out["ram_gb"] == 0.0
    assert out["download_mb"] == 0
    assert out["tier"] == "custom"


def test_prompt_custom_tag_empty_input_returns_none():
    """Pin: empty / whitespace-only input cancels the custom path —
    main() loops back to the table rather than passing a blank tag
    into `ollama pull` (which would fail with a confusing error)."""
    import importlib.util
    from importlib.machinery import SourceFileLoader
    here = (Path(__file__).resolve().parent.parent
            / "tools" / "aider_setup_wizard")
    loader = SourceFileLoader("_aider_wizard", str(here))
    spec = importlib.util.spec_from_loader("_aider_wizard", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    class _FakePrompt:
        @staticmethod
        def ask(*_a, **_kw):
            return "   "

    mod.Prompt = _FakePrompt
    assert mod.prompt_custom_tag(mod.Console(quiet=True)) is None


def test_wizard_pull_failure_returns_nonzero(tmp_path):
    """Pin: when `ollama pull` exits non-zero (network down, bad tag),
    wizard must NOT write the model into options.json — that would
    leave the user with a config pointing at a not-actually-installed
    tag (regression of BUG#19)."""
    result = _run_wizard(
        tmp_path, "--headless",
        list_models=[],  # nothing installed → wizard will try to pull
        pull_rc=2,        # but our fake `ollama pull` fails
    )
    assert result.returncode != 0
    options_path = tmp_path / ".config" / "bterminal" / "options.json"
    assert not options_path.exists(), (
        "wizard wrote options.json despite a failed `ollama pull` — "
        "this would persist a broken default and re-trigger BUG#19 on "
        "the next aider spawn."
    )
