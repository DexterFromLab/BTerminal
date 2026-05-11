"""bterminal.providers.base — AI CLI provider abstraction core.

Defines the AIProvider ABC + supporting dataclasses for capability
flags and display metadata. Concrete providers (claude, copilot, ...)
subclass AIProvider and live in sibling modules. ProviderRegistry in
bterminal.providers loads them from providers.json (T1.2).

Dataclasses:
    ProviderDisplay      — icon / short_label / long_label / color
    ProviderCapabilities — boolean flags + path templates
    SessionStats         — output of parse_session_stats()

ABC:
    AIProvider — name/display/capabilities/pricing + abstract methods
                 for binary lookup, argv build, log parsing, idle detect.

Per docs/cli-provider-abstraction-implementation-plan.md task T1.1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# defaults/ lives next to the bterminal package (sibling of bterminal/),
# same convention as defaults/license/ and defaults/skills/.
_DEFAULTS_DIR = Path(__file__).resolve().parent.parent.parent / "defaults"


def resolve_provider_icon_path(icon_path: Optional[str]) -> Optional[str]:
    """Resolve a ProviderDisplay.icon_path to an absolute file path.

    `icon_path` may be:
      - None → return None (no icon configured)
      - absolute path → return as-is if file exists
      - relative path → joined with defaults/ root

    Returns None when the path doesn't resolve to an existing file —
    callers should fall back to the emoji `icon` field then. Task #57
    (2026-05-07) introduces this so the sidebar / dialog can prefer
    a real logo when shipped, without crashing if the file is missing
    (packaging bug, manual delete).
    """
    if not icon_path:
        return None
    p = Path(icon_path)
    if not p.is_absolute():
        p = _DEFAULTS_DIR / icon_path
    return str(p) if p.is_file() else None


@dataclass(frozen=True)
class ProviderDisplay:
    """Visual identity of a provider — used for tab markers (R7a).

    Task #57 (2026-05-07): added icon_path so providers can ship a
    real logo (SVG bundled in defaults/icons/) instead of an emoji.
    The emoji `icon` field stays as fallback for environments where
    the SVG can't be loaded (rsvg missing, file deleted, packaging
    error). Renderers should prefer the pixbuf path when it resolves;
    fall back to emoji otherwise.
    """

    icon: str          # emoji fallback shown when icon_path doesn't resolve
    short_label: str   # e.g. "Claude", "Copilot"
    long_label: str    # e.g. "Claude Code", "GitHub Copilot CLI"
    color: str         # hex color (e.g. "#89b4fa") for tab underline
    icon_path: Optional[str] = None   # path under defaults/ (e.g. "icons/claude.svg")


@dataclass(frozen=True)
class ProviderCapabilities:
    """Per-provider feature flags + path templates.

    A False flag means BTerminal must skip the corresponding code path
    entirely (no stats bar, no auto-trigger, etc.). Path templates use
    {sanitized_cwd} / {session_id} placeholders that the provider
    expands at runtime.

    See docs/REQUIREMENTS.md R4a for the canonical schema.
    """

    intro_prompt: bool = False
    resume_flag: bool = False
    continue_flag: bool = False
    skip_permissions: bool = False
    granular_permissions: bool = False
    supports_sudo: bool = False
    session_log: bool = False
    session_log_path: Optional[str] = None
    session_index_db: bool = False
    session_index_db_path: Optional[str] = None
    usage_api: bool = False
    usage_api_url: Optional[str] = None
    oauth_creds_file: Optional[str] = None
    cost_in_log: bool = False
    rules_inject: bool = False
    task_auto_trigger: bool = False
    stats_bar: bool = False
    stats_bar_no_plan_usage: bool = False
    plan_mode: bool = False
    autopilot: bool = False
    mcp_support: bool = False
    context_file: Optional[str] = None
    context_file_cumulative: bool = False
    ready_marker: Optional[str] = None
    default_model: Optional[str] = None
    # Task #3 (#75 in audit doc): set when the provider relies on a
    # local LLM daemon (Ollama / llama.cpp serve / vLLM) and BT should
    # surface it in the InstallerWizard #77 + OptionsDialog #79 model
    # manager. None means "no local backend involved" (Claude/Copilot).
    local_endpoint_url: Optional[str] = None


@dataclass
class SessionStats:
    """Provider-agnostic output of AIProvider.parse_session_stats().

    Tokens are integer counts; cost is USD float. Providers that don't
    track a particular metric leave it at the default zero/None value.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    response_count: int = 0
    model: Optional[str] = None


class AIProvider(ABC):
    """Abstract base for AI CLI providers (claude, copilot, aider, ...).

    Concrete subclasses must populate `name`, `display`, `capabilities`
    (and optionally `pricing`) and implement the abstract methods.
    Default implementations are provided for `fetch_plan_usage`,
    `detect_idle`, and `get_dialog_schema` so providers without those
    features don't need to override them.
    """

    name: str
    display: ProviderDisplay
    capabilities: ProviderCapabilities
    pricing: dict

    @abstractmethod
    def find_binary(self) -> Optional[str]:
        """Return absolute path to the provider's CLI binary, or None."""

    @abstractmethod
    def build_argv(self, config: dict, intro_prompt: str) -> list[str]:
        """Build argv list for spawn (first element = binary path).

        config        — session config dict (project_dir, provider_options, ...)
        intro_prompt  — pre-rendered intro text (may be empty)
        """

    @abstractmethod
    def session_log_glob(self, project_dir: str) -> Optional[str]:
        """Return a glob matching the provider's session log files,
        or None if capabilities.session_log is False."""

    @abstractmethod
    def parse_session_stats(self, log_path: str) -> SessionStats:
        """Parse a session log file and return accumulated stats."""

    def fetch_plan_usage(self) -> Optional[dict]:
        """Return plan-usage data (5h/7d windows) or None.

        Default: None — providers without a usage API leave it unset.
        Override where capabilities.usage_api == True (e.g. Claude).
        """
        return None

    def inject_intro_prompt(self, terminal: Any, intro_prompt: str) -> None:
        """Deliver the intro prompt for providers that can't take it via argv.

        Default: no-op — Claude / Copilot already appended `intro_prompt`
        to argv in `build_argv`, so the CLI sees it on stdin before its
        first prompt iteration.

        Aider has no `--message-init` flag, so AiderProvider overrides
        this to schedule a delayed `terminal.feed_child(...)` via GLib —
        the PTY needs ~2s to settle past aider's banner before it
        accepts piped input cleanly (BUG#27).
        """
        return None

    def detect_idle(
        self,
        terminal: Any,
        session_id: Optional[str],
        timeout_s: float = 10.0,
    ) -> bool:
        """Return True if the session is idle (ready for next prompt).

        Default: assume idle (caller is expected to debounce already).
        Override in providers that emit a deterministic ready marker
        (e.g. Claude `system.stop` in stream-json, Copilot tail-f
        events.jsonl in T4.1).
        """
        return True

    def get_dialog_schema(self) -> list[tuple[str, str, str]]:
        """Return [(key, widget_type, label), ...] for the
        provider-specific section of AISessionDialog (T2.6).

        widget_type ∈ {"checkbox", "combo", "text", "textarea"}.
        Default: empty — provider has no extra fields.
        """
        return []


__all__ = [
    "AIProvider",
    "ProviderCapabilities",
    "ProviderDisplay",
    "SessionStats",
]
