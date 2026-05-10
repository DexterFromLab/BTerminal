"""Forward compat: aider 1.x argv changes / version detection
(#48 / #120, audit § 6.5 #21).

`AiderProvider.build_argv` hardcodes flags assumed by current
aider (1.x as of writing): `--model`, `--openai-api-base`,
`--openai-api-key`, `--no-stream`, `--no-show-model-warnings`,
`--restore-chat-history`, `--yes-always`. Future versions may
rename / remove these.

Three decision branches from auto-trigger plan:
  (a) aider 0.x — lacks `--no-show-model-warnings` (added in
      0.42). Today's BT spawn would fail with "unknown option".
  (b) 1.x renames a flag — same outcome: aider rejects argv.
  (c) 2.x removes `--openai-api-base` in favor of `--provider` —
      again, aider crashes on argv parse.

This task lays the GROUNDWORK for a future shim:
  - Adds `detect_aider_version(binary_path)` that probes
    `aider --version` and parses (major, minor, patch).
  - Pins the canonical flag set in `build_argv` source so a
    future shim can use version-aware overrides via
    `_argv_spec` defaults.
  - Pins the failure mode: pre-#120, aider crashes loudly with
    its own argv parser error in VTE — BT itself stays alive.

The shim implementation (branching `build_argv` on detected
version) is a follow-up task. This file pins the foundation +
documents the migration path.

Manual VM smoke (`pipx install aider-chat==0.x` then `==latest`,
spawn each) is documented in tests/manual/README.md.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.aider import (
    detect_aider_version,
    AiderProvider,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
AIDER_PROVIDER = REPO_ROOT / "bterminal" / "providers" / "aider.py"


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── detect_aider_version: parse output forms ───────────────────────────


def _fake_run(stdout: str = "", stderr: str = "",
                returncode: int = 0):
    """Helper to build a CompletedProcess-like mock for
    subprocess.run patches."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


@pytest.mark.parametrize("output, expected", [
    ("aider 0.85.0\n",            (0, 85, 0)),
    ("aider v0.85.0\n",           (0, 85, 0)),
    ("aider-chat 1.2.3\n",        (1, 2, 3)),
    ("aider 0.85.0+dev\n",        (0, 85, 0)),
    ("aider 1.0.0-rc1\n",         (1, 0, 0)),
    ("aider version 2.0.5\n",     (2, 0, 5)),
])
def test_detect_aider_version_parses_canonical_outputs(output, expected):
    """The helper handles every common output form (with/without
    leading 'v', with '+dev' suffix, with 'aider-chat' name,
    rc1 prerelease)."""
    with patch("subprocess.run",
                return_value=_fake_run(stdout=output)):
        result = detect_aider_version("/tmp/aider")
    assert result == expected


def test_detect_aider_version_returns_none_on_missing_binary():
    """Caller passes None (no binary resolved) → no probe
    attempted, return None."""
    assert detect_aider_version(None) is None


def test_detect_aider_version_returns_none_when_subprocess_fails():
    """`FileNotFoundError` (binary went missing between resolve
    and probe) → graceful None, no exception."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert detect_aider_version("/tmp/aider") is None


def test_detect_aider_version_returns_none_on_timeout():
    """Subprocess hangs → TimeoutExpired → None."""
    with patch("subprocess.run",
                side_effect=subprocess.TimeoutExpired("aider", 5)):
        assert detect_aider_version("/tmp/aider") is None


def test_detect_aider_version_returns_none_on_oserror():
    """`OSError` (EACCES, etc.) → None, no exception."""
    with patch("subprocess.run",
                side_effect=OSError(13, "permission denied")):
        assert detect_aider_version("/tmp/aider") is None


def test_detect_aider_version_returns_none_when_no_semver_in_output():
    """Output that doesn't match \\d+.\\d+.\\d+ → None."""
    for bogus in ["aider", "Some random text", "version unknown",
                   "0.85"]:  # bare two-segment, no patch
        with patch("subprocess.run",
                    return_value=_fake_run(stdout=bogus)):
            assert detect_aider_version("/tmp/aider") is None, (
                f"unexpected version parse from {bogus!r}"
            )


def test_detect_aider_version_falls_back_to_stderr_when_stdout_empty():
    """Some `aider --version` builds emit to stderr (especially
    when the binary is a wrapper). Helper checks both streams."""
    with patch("subprocess.run",
                return_value=_fake_run(
                    stdout="", stderr="aider 1.2.3\n")):
        assert detect_aider_version("/tmp/aider") == (1, 2, 3)


def test_detect_aider_version_picks_first_semver_when_multiple():
    """If the version output happens to contain multiple semver-
    like triples (e.g. python version + aider version), match
    the FIRST occurrence. Pin so refactor toward 'last match' is
    explicit."""
    with patch("subprocess.run",
                return_value=_fake_run(
                    stdout="aider 0.85.0  Python 3.12.3\n")):
        # First match is aider's own version
        assert detect_aider_version("/tmp/aider") == (0, 85, 0)


# ─── Real-binary integration (skipped if aider not installed) ───────────


def test_detect_aider_version_against_real_binary_if_present():
    """If aider IS installed locally, the helper returns a real
    semver tuple. Skipped on machines without aider — gives a
    real-world signal where available."""
    aider_bin = shutil.which("aider")
    if not aider_bin:
        pytest.skip("aider not installed locally")
    result = detect_aider_version(aider_bin)
    # Real version returns a 3-tuple of ints (or None if the
    # local binary's --version is unusual)
    assert result is None or (
        isinstance(result, tuple)
        and len(result) == 3
        and all(isinstance(n, int) for n in result)
    )


# ─── Pin canonical flag set in build_argv (current 1.x assumed) ────────


CANONICAL_FLAGS_TODAY = [
    "--model",
    "--openai-api-base",
    "--openai-api-key",
    "--no-stream",
    "--no-show-model-warnings",
]


def test_build_argv_emits_canonical_flag_set_for_current_version(
        tmp_path):
    """Pin the EXACT flag set BT emits today. If this test fails
    after a refactor, you're either:
      (1) Changing the flags BT supports → update assertion +
          documentation.
      (2) Adding a version-aware shim → restructure to per-
          version expectations."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    aider._binary_spec["binary"] = "/tmp/aider"
    argv = aider.build_argv(
        {"project_dir": str(tmp_path), "provider_options": {}},
        intro_prompt="",
    )
    for flag in CANONICAL_FLAGS_TODAY:
        assert flag in argv, (
            f"canonical flag {flag!r} not in argv: {argv}"
        )


@pytest.mark.parametrize("flag", CANONICAL_FLAGS_TODAY)
def test_each_canonical_flag_documented_in_provider_source(flag):
    """Source-grep: each flag appears in aider.py source so a
    reader can find it. Documents the flag's role in the build
    pipeline."""
    src = AIDER_PROVIDER.read_text()
    assert flag in src, (
        f"flag {flag!r} not referenced in aider.py — fragile "
        f"assumption left undocumented"
    )


# ─── Branch (a): aider 0.x lacks --no-show-model-warnings ──────────────


def test_no_show_model_warnings_is_a_post_0_42_flag():
    """Pin awareness: `--no-show-model-warnings` was added in
    aider 0.42+. Pre-0.42 binaries crash on this flag.

    Today's BT defaults assume this flag exists. The shim
    follow-up should detect version < (0, 42, 0) and DROP this
    flag from argv. Pin the threshold so the future shim
    knows what to gate."""
    # The flag in defaults.json's tui_safe list
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    tui_flags = aider._argv_spec.get("tui_safe", [])
    assert "--no-show-model-warnings" in tui_flags
    # Pin: tui_safe is the override hook for shim. Future
    # version-aware shim filters this list per version.


def test_aider_0_x_version_detection_works_for_shim_threshold():
    """Verify the helper correctly parses pre-0.42 versions so
    the shim can branch on them. Pin so a refactor doesn't break
    the threshold check."""
    with patch("subprocess.run",
                return_value=_fake_run(stdout="aider 0.41.5\n")):
        ver = detect_aider_version("/tmp/aider")
    assert ver == (0, 41, 5)
    # Comparison works: < (0, 42, 0)
    assert ver < (0, 42, 0)


# ─── Branch (b): 1.x renames a flag — version detection groundwork ─────


def test_aider_1_x_version_detection_works_for_shim_threshold():
    """Pin: the helper parses 1.x versions. Future shim can
    detect (major == 1) and apply 1.x-specific overrides."""
    with patch("subprocess.run",
                return_value=_fake_run(stdout="aider 1.5.2\n")):
        ver = detect_aider_version("/tmp/aider")
    assert ver == (1, 5, 2)
    assert ver[0] == 1


def test_argv_spec_is_overridable_via_user_providers_json(tmp_path):
    """Pin: `_argv_spec` (which holds tui_safe etc.) comes from
    defaults.json + user override merge. A user with aider 0.x
    can manually override via `~/.config/bterminal/providers.json`
    to drop the unsupported flag — without waiting for a BT
    update.

    This is the manual escape hatch until the version-aware
    shim ships."""
    user_path = tmp_path / "providers.json"
    import json
    user_path.write_text(json.dumps({
        "providers": {
            "aider": {
                "argv": {
                    "tui_safe": ["--no-stream"],  # drop --no-show-model-warnings
                }
            }
        }
    }))
    config = load_providers_config(user_path=user_path)
    reg = ProviderRegistry(config=config)
    aider = reg.get("aider")
    aider._binary_spec["binary"] = "/tmp/aider"
    argv = aider.build_argv(
        {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="",
    )
    # User's reduced flag list applied
    assert "--no-stream" in argv
    assert "--no-show-model-warnings" not in argv


# ─── Branch (c): 2.x removes --openai-api-base ─────────────────────────


def test_api_base_flag_overridable_per_user_for_2_x_compat(tmp_path):
    """Pin: the api_base argv template (`["--openai-api-base",
    "{url}"]`) is overridable via user override. A user with
    aider 2.x (which uses `--provider`) can swap the template:

        argv.api_base = ["--provider", "openai/{url}"]

    Pin so the shim hook stays available."""
    user_path = tmp_path / "providers.json"
    import json
    user_path.write_text(json.dumps({
        "providers": {
            "aider": {
                "argv": {
                    "api_base": ["--provider",
                                  "openai-compat:{url}"],
                }
            }
        }
    }))
    config = load_providers_config(user_path=user_path)
    reg = ProviderRegistry(config=config)
    aider = reg.get("aider")
    aider._binary_spec["binary"] = "/tmp/aider"
    argv = aider.build_argv(
        {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="",
    )
    # User's 2.x-compat flag applied; --openai-api-base gone
    assert "--provider" in argv
    assert "openai-compat:http://localhost:11434/v1" in argv
    assert "--openai-api-base" not in argv


def test_aider_2_x_version_detection_distinct_from_1_x():
    """Pin: helper differentiates 2.x from 1.x. Future shim can
    branch."""
    with patch("subprocess.run",
                return_value=_fake_run(stdout="aider 2.0.0\n")):
        ver = detect_aider_version("/tmp/aider")
    assert ver == (2, 0, 0)
    assert ver[0] == 2


# ─── Failure mode: when aider crashes on unknown flag ───────────────────


def test_aider_crash_on_unknown_flag_does_not_take_down_bt():
    """Pin via source-grep: `spawn_ai_cli` calls feed_child for
    spawn — it doesn't propagate aider's exit code. Aider can
    crash with `error: unknown option --no-show-model-warnings`
    and BT's tab shows that error in VTE. The TerminalTab
    survives via `exec bash` in the spawn script (#101 baseline)."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
           ).read_text()
    spawn_idx = src.find("def _build_spawn_script")
    spawn_end = src.find("\n    def ", spawn_idx + 1)
    body = src[spawn_idx:spawn_end]
    # The spawn script ends with `exec bash` so a crashed aider
    # leaves a usable shell — user can inspect error and re-run
    assert "exec bash" in body, (
        "spawn script no longer falls back to bash on aider exit — "
        "version-mismatch crashes would close the tab silently"
    )


def test_aider_provider_does_not_currently_probe_version_at_runtime():
    """Pin: today AiderProvider.find_binary / build_argv don't
    invoke detect_aider_version. The helper exists for future
    use. Lifting this pin = the shim has shipped."""
    src = AIDER_PROVIDER.read_text()
    # find_binary doesn't call the version helper
    fn_start = src.find("def find_binary")
    fn_end = src.find("\n    def ", fn_start + 1)
    find_body = src[fn_start:fn_end]
    assert "detect_aider_version" not in find_body

    # build_argv doesn't either
    fn_start = src.find("def build_argv")
    fn_end = src.find("\n    def ", fn_start + 1)
    build_body = src[fn_start:fn_end]
    assert "detect_aider_version" not in build_body, (
        "build_argv now uses detect_aider_version — shim shipped, "
        "lift these pins + add version-aware behaviour tests"
    )


# ─── Migration marker: shim landing flips assertions ───────────────────


def test_helper_exists_and_is_module_level_export():
    """The helper is importable from bterminal.providers.aider —
    pinning the API surface for future shim consumers (which may
    live in a separate compat module)."""
    from bterminal.providers import aider as aider_module
    assert hasattr(aider_module, "detect_aider_version")
    assert callable(aider_module.detect_aider_version)


def test_helper_signature_takes_optional_binary_path():
    """Pin: signature is `detect_aider_version(binary_path:
    Optional[str] = None)`. Caller can pass None for graceful
    'not installed' check."""
    import inspect
    sig = inspect.signature(detect_aider_version)
    params = list(sig.parameters.values())
    assert len(params) == 1
    assert params[0].name == "binary_path"
    # Default = None
    assert params[0].default is None


def test_helper_returns_optional_tuple_of_three_ints():
    """Pin: return type is `Optional[tuple[int, int, int]]`.
    Pin so a refactor that returns a packaging.Version object or
    similar would fail this guard + force the caller updates."""
    with patch("subprocess.run",
                return_value=_fake_run(stdout="aider 1.0.0\n")):
        out = detect_aider_version("/tmp/aider")
    assert isinstance(out, tuple)
    assert len(out) == 3
    assert all(isinstance(x, int) for x in out)


# ─── Pure-helper isolation: no GTK / config dependency ─────────────────


def test_helper_does_not_import_gtk_or_terminal_tab():
    """Pin: detect_aider_version is pure — no GTK / VTE
    imports. Safe to call from any context (REST, scripts,
    doctor command)."""
    src = AIDER_PROVIDER.read_text()
    fn_start = src.find("def detect_aider_version")
    fn_end = src.find("\ndef ", fn_start + 1)
    if fn_end < 0:
        fn_end = src.find("\nclass ", fn_start + 1)
    body = src[fn_start:fn_end]
    forbidden = ["Gtk", "Vte", "GLib", "TerminalTab"]
    for pat in forbidden:
        assert pat not in body, (
            f"detect_aider_version imports/references {pat!r} — "
            f"adds a heavy dependency to a probe helper"
        )


def test_helper_uses_short_subprocess_timeout():
    """Pin: 5s timeout on aider --version. Without it, a stuck
    aider (e.g. waiting for stdin) would hang the probe forever
    — and any caller (UI / doctor command) would block."""
    src = AIDER_PROVIDER.read_text()
    fn_start = src.find("def detect_aider_version")
    fn_end = src.find("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "timeout=5" in body
