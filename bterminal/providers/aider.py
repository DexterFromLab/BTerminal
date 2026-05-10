"""bterminal.providers.aider — open-source AI coding assistant provider.

Aider (https://aider.chat) is a Python CLI that pairs with a local or
remote LLM via OpenAI-compatible chat completions. We ship it as the
3rd built-in provider so users can:
  - Try BT against a local model running in Ollama (zero recurring cost)
  - Stay open-source-only when corporate policy bans Anthropic/GitHub
  - Compare output of the same project between Claude / Copilot / Aider

Defaults wire it to a local Ollama daemon at :11434 with Qwen-Coder 0.5B
as the smallest sensible model that fits ~1GB RAM.

Audit doc § 1, § 9. Capability matrix:
  intro_prompt=true (via PTY stdin feed — Aider has no --message-init)
  session_log=true   (.aider.chat.history.md in project_dir)
  cost_in_log=false  (Aider doesn't write costs; tokens approx via grep)
  rules_inject=true  (PTY feed_child works identically across providers)
  task_auto_trigger=true (idle = VTE silent, no special ready marker)
  stats_bar=true     (tokens-only mode — no plan-usage gauge possible)
  local_endpoint_url="http://localhost:11434/v1" (#75)
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from typing import Any, Optional

from bterminal.providers.base import (
    AIProvider,
    ProviderCapabilities,
    ProviderDisplay,
    SessionStats,
)


_DEFAULT_API_BASE = "http://localhost:11434/v1"
_DEFAULT_MODEL = "openai/qwen2.5-coder:0.5b"
_DEFAULT_API_KEY_DUMMY = "dummy"  # local Ollama ignores this; aider requires non-empty


def detect_aider_version(binary_path: Optional[str] = None
                          ) -> Optional[tuple[int, int, int]]:
    """Probe `aider --version` and return parsed (major, minor, patch).

    Returns None when:
      - binary_path is None (caller didn't resolve a binary)
      - subprocess.run fails (binary missing, EACCES, etc.)
      - output doesn't match the expected semver pattern

    #120 (audit § 6.5 #21): future shim layer can branch on the
    returned tuple to skip flags introduced in newer aider
    versions (e.g. `--no-show-model-warnings` is 0.42+ only).
    Today this helper is a probe — callers don't yet use its
    output, but the parsing contract is pinned by tests so the
    eventual shim has a stable foundation.

    Aider's `--version` output forms observed in the wild:
        aider 0.85.0
        aider v0.85.0
        aider-chat 1.2.3
        aider 0.85.0+dev (dev/local builds)
    """
    if not binary_path:
        return None
    try:
        result = subprocess.run(
            [binary_path, "--version"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    raw = (result.stdout or result.stderr or "").strip()
    # Match the first semver-like triple in the output. Tolerates
    # a leading 'v' (e.g. `aider v0.85.0`) by allowing the digit
    # to start anywhere after a non-digit char (or string start).
    # Ignores '+devbuild' suffixes by not requiring a trailing
    # word boundary on the patch number.
    m = re.search(r"(?:^|[^\d])(\d+)\.(\d+)\.(\d+)", raw)
    if not m:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (TypeError, ValueError):
        return None


class AiderProvider(AIProvider):
    """Aider CLI provider (open-source, local-LLM-friendly)."""

    name = "aider"

    def __init__(self, config: dict):
        self.display = ProviderDisplay(**config["display"])
        self.capabilities = ProviderCapabilities(**config["capabilities"])
        self.pricing = config.get("pricing", {})
        self._argv_spec = config.get("argv", {})
        self._binary_spec = config.get("binary", {})

    # ─── Binary discovery ──────────────────────────────────────────────────

    def find_binary(self) -> Optional[str]:
        """Return the first executable path matching aider, or None.

        Aider is typically installed via:
          - pip install aider-chat → ~/.local/bin/aider
          - pipx install aider-chat → ~/.local/bin/aider (pipx symlink)
          - system pkg manager → /usr/bin/aider
        """
        for path_spec in self._binary_spec.get("search_paths", []):
            expanded = os.path.expanduser(path_spec)
            for hit in glob.glob(expanded):
                if os.path.isfile(hit) and os.access(hit, os.X_OK):
                    return hit
        return shutil.which("aider")

    # ─── argv builder ──────────────────────────────────────────────────────

    def build_argv(self, config: dict, intro_prompt: str) -> list[str]:
        """Compose argv for `aider` spawn.

        Convention:
          aider --model openai/qwen2.5-coder:0.5b
                --openai-api-base http://localhost:11434/v1
                --openai-api-key dummy
                --no-stream
                --no-show-model-warnings
                <project_dir>            # positional cwd hint

        Intro prompt is NOT passed via argv — Aider has no
        --message-init flag. BT injects it through PTY feed_child()
        right after spawn, same path Claude/Copilot use for rules
        injection (terminal_tab.py:_do_inject_rules).
        """
        binary = self.find_binary()
        if not binary:
            return []

        argv: list[str] = [binary]
        argv.extend(self._binary_spec.get("argv_prefix", []))

        opts = config.get("provider_options") or config

        # --model resolution priority (highest to lowest):
        #   1. Per-session opts["model"] (user picked in dialog)
        #   2. Global default from OptionsDialog "Set as default" (#7)
        #   3. Provider's capabilities.default_model
        #   4. Hardcoded _DEFAULT_MODEL
        model = opts.get("model")
        if not model:
            try:
                from bterminal.config import _OPTIONS
                mapping = _OPTIONS.get(
                    "default_local_model_for_provider") or {}
                model = mapping.get(self.name)
            except Exception:
                model = None
        model = (model
                 or self.capabilities.default_model
                 or _DEFAULT_MODEL)
        model_tpl = self._argv_spec.get("model", ["--model", "{model}"])
        argv.extend(s.format(model=model) for s in model_tpl)

        # --openai-api-base / --openai-api-key (local LLM endpoint)
        endpoint = (opts.get("local_endpoint_url")
                    or self.capabilities.local_endpoint_url
                    or _DEFAULT_API_BASE)
        api_base_tpl = self._argv_spec.get(
            "api_base", ["--openai-api-base", "{url}"])
        argv.extend(s.format(url=endpoint) for s in api_base_tpl)

        api_key = opts.get("api_key") or _DEFAULT_API_KEY_DUMMY
        api_key_tpl = self._argv_spec.get(
            "api_key", ["--openai-api-key", "{key}"])
        argv.extend(s.format(key=api_key) for s in api_key_tpl)

        # TUI-friendly default flags (audit § 10 alt-screen lesson):
        # --no-stream so VTE main-screen catches every line; aider
        # respects user's existing scrollback patterns.
        argv.extend(self._argv_spec.get(
            "tui_safe", ["--no-stream", "--no-show-model-warnings"]))

        # Resume / continue — aider has --restore-chat-history
        if opts.get("resume") and self.capabilities.resume_flag:
            argv.extend(self._argv_spec.get(
                "resume", ["--restore-chat-history"]))

        # Skip permissions — aider --yes-always (auto-confirm edits)
        if opts.get("skip_permissions") and self.capabilities.skip_permissions:
            argv.extend(self._argv_spec.get("yolo", ["--yes-always"]))

        # Project dir is passed as a positional argument (aider's cwd
        # detection); BT also chdir's the spawn so this is belt-and-
        # suspenders. project_dir absent → aider uses spawn cwd.
        project_dir = config.get("project_dir")
        if project_dir:
            argv.append(project_dir)

        # NOTE intro_prompt intentionally not appended — see docstring.
        return argv

    # ─── Session log ───────────────────────────────────────────────────────

    def session_log_glob(self, project_dir: str) -> Optional[str]:
        """Aider writes a single rolling chat history file per project.

        Default: <project_dir>/.aider.chat.history.md
        Path can be relocated via .aider.conf.yml or env, but BT
        expects the default. Returns None when the capability is off.
        """
        if not self.capabilities.session_log or not project_dir:
            return None
        template = self.capabilities.session_log_path or \
                   "{project_dir}/.aider.chat.history.md"
        return template.format(project_dir=project_dir.rstrip("/"))

    def parse_session_stats(self, log_path: str) -> SessionStats:
        """Approximate token + response counts from a markdown chat log.

        Aider doesn't structure stats — it writes free-form markdown
        with occasional "Tokens: 1.2k sent, 350 received" lines. We
        grep for those and sum. Cost is left at 0.0 because Aider
        doesn't write per-call costs to the chat log (would require
        intercepting model API call site). When that grep finds
        nothing (older aider versions, or fresh log), we fall back to
        counting role markers as a rough response_count signal.
        """
        stats = SessionStats()
        if not log_path or not os.path.isfile(log_path):
            return stats
        try:
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return stats

        # Format examples in aider chat history:
        #   "> Tokens: 1.5k sent, 234 received."
        #   "Tokens: 12,345 sent, 5678 received."
        token_re = re.compile(
            r"Tokens:\s*([\d,.]+)\s*([kKmM]?)\s*sent[^\d]*"
            r"([\d,.]+)\s*([kKmM]?)\s*received",
            re.IGNORECASE,
        )
        for m in token_re.finditer(content):
            stats.input_tokens += _parse_num_with_suffix(m.group(1), m.group(2))
            stats.output_tokens += _parse_num_with_suffix(m.group(3), m.group(4))

        # Aider role markers in markdown: lines starting with "#### " are
        # user turns; "**Aider**:" or "## Assistant" indicate replies.
        # Count user turns = response_count (one reply per ask).
        stats.response_count = sum(
            1 for line in content.splitlines() if line.startswith("#### ")
        )

        # Best-effort model attribution — last `--model` occurrence wins
        m = re.search(r"--model\s+([\w./:-]+)", content)
        if m:
            stats.model = m.group(1)
        elif self.capabilities.default_model:
            stats.model = self.capabilities.default_model

        return stats

    # ─── Idle detection ────────────────────────────────────────────────────

    def detect_idle(
        self,
        terminal: Any,
        session_id: Optional[str],
        timeout_s: float = 10.0,
    ) -> bool:
        """Aider has no deterministic ready marker. Trust caller's
        VTE-silent debounce (BT idle_check_tick already does ~10s
        quiet detection)."""
        return True


def _parse_num_with_suffix(num_str: str, suffix: str) -> int:
    """'1.5' + 'k' → 1500. Defensive: returns 0 on parse failure."""
    try:
        n = float(num_str.replace(",", ""))
    except ValueError:
        return 0
    suffix = suffix.lower()
    if suffix == "k":
        n *= 1_000
    elif suffix == "m":
        n *= 1_000_000
    return int(n)


__all__ = ["AiderProvider"]
