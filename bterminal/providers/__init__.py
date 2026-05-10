"""bterminal.providers — AI CLI provider abstraction.

Public surface:
    AIProvider, ProviderCapabilities, ProviderDisplay, SessionStats
        — base ABC + dataclasses (T1.1).
    load_providers_config(user_path)
        — bundled defaults.json + optional user override merge (T1.2).
    ClaudeProvider (T1.3) / CopilotProvider (T1.4)
        — concrete implementations.
    ProviderRegistry (T1.5)
        — singleton mapping name → instance, auto-loaded from
        defaults.json + ~/.config/bterminal/providers.json on first
        access. Tests use reset_registry() to get a clean slate.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from bterminal.providers.base import (
    AIProvider,
    ProviderCapabilities,
    ProviderDisplay,
    SessionStats,
)
from bterminal.providers.aider import AiderProvider
from bterminal.providers.claude import ClaudeProvider
from bterminal.providers.copilot import CopilotProvider

# Bundled defaults — sits next to this __init__.py.
DEFAULTS_PATH = Path(__file__).parent / "defaults.json"

# Default location for user override (set via env to relocate in tests).
USER_OVERRIDE_PATH = Path(
    os.path.expanduser("~/.config/bterminal/providers.json")
)

# Maps provider name → concrete class. Extended by future tasks
# (e.g. T5+ AiderProvider) or by callers via ProviderRegistry.register().
_PROVIDER_CLASSES: dict[str, type[AIProvider]] = {
    "claude": ClaudeProvider,
    "copilot": CopilotProvider,
    "aider": AiderProvider,
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into `base`, returning a NEW dict.

    Lists are replaced wholesale (not concatenated) — user override of
    `binary.search_paths` fully replaces the bundled list, which is the
    expected semantic for "I have copilot in /opt/custom/bin".
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_providers_config(user_path: Optional[Path] = None) -> dict:
    """Load providers config: bundled defaults + optional user override.

    user_path:  optional override file. If it doesn't exist or is
                corrupt, defaults are used and a warning is printed to
                stderr (fail-open).

    Returns:    {"$schema": ..., "default_provider": ..., "providers": {...}}
    """
    with open(DEFAULTS_PATH, encoding="utf-8") as fh:
        config = json.load(fh)

    if user_path is None or not user_path.exists():
        return config

    try:
        with open(user_path, encoding="utf-8") as fh:
            override = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[bterminal] WARN: failed to load providers override "
            f"{user_path}: {exc} — using bundled defaults",
            file=sys.stderr,
        )
        return config

    return _deep_merge(config, override)


# ─── Registry ───────────────────────────────────────────────────────────────


class ProviderRegistry:
    """Holds instantiated AIProvider objects keyed by name.

    Accessed via the module-level `get_registry()` singleton (auto-loads
    on first call). Tests construct standalone instances with the
    explicit `config` arg to stay isolated from the real ~/.config.
    """

    def __init__(self, config: Optional[dict] = None):
        """Build a registry from `config` (or freshly-loaded defaults
        + user override if `config` is None). Unknown provider names
        in the config (no class registered for them) are skipped with
        a stderr warning rather than crashing — lets future-version
        configs forward-compat with older BTerminal builds."""
        if config is None:
            user_path = USER_OVERRIDE_PATH if USER_OVERRIDE_PATH.exists() else None
            config = load_providers_config(user_path=user_path)
        self._config = config
        self._providers: dict[str, AIProvider] = {}
        self._default_name: str = config.get("default_provider", "claude")

        for name, slice_ in config.get("providers", {}).items():
            cls = _PROVIDER_CLASSES.get(name)
            if cls is None:
                print(
                    f"[bterminal] WARN: provider '{name}' has config but "
                    f"no class registered — skipping. Use "
                    f"ProviderRegistry.register() to add it.",
                    file=sys.stderr,
                )
                continue
            try:
                self._providers[name] = cls(slice_)
            except Exception as exc:
                print(
                    f"[bterminal] WARN: failed to instantiate provider "
                    f"'{name}': {exc} — skipping",
                    file=sys.stderr,
                )

    def register(self, provider: AIProvider) -> None:
        """Add (or replace) a provider instance under provider.name."""
        self._providers[provider.name] = provider

    def get(self, name: str) -> AIProvider:
        """Return the provider instance for `name`. Raises KeyError if
        the name isn't registered."""
        try:
            return self._providers[name]
        except KeyError:
            raise KeyError(
                f"Unknown provider '{name}'. Registered: "
                f"{sorted(self._providers.keys())}"
            ) from None

    def has(self, name: str) -> bool:
        return name in self._providers

    def all(self) -> list[AIProvider]:
        """Return ALL registered providers in alphabetical order.

        Includes user-disabled ones (task #11 / #83). Existing sessions
        with a saved provider field keep rendering even when the user
        hid that provider in OptionsDialog — only the 'create new
        session' UI consults `enabled()` to filter the dropdown.
        """
        return [self._providers[k] for k in sorted(self._providers)]

    def enabled(self) -> list[AIProvider]:
        """Return providers NOT marked disabled in
        _OPTIONS['disabled_providers'] (task #11 / #83).

        Used by AISessionDialog to populate the provider dropdown +
        ProviderManager UI to render the live list. Empty list means
        the user disabled every provider — caller decides what to do
        (typically: show a 'no providers enabled' notice).
        """
        try:
            from bterminal.config import _OPTIONS
            disabled = set(_OPTIONS.get("disabled_providers") or [])
        except Exception:
            disabled = set()
        return [p for p in self.all() if p.name not in disabled]

    def is_enabled(self, name: str) -> bool:
        """Single-provider check. False when name in disabled_providers,
        True when registered + enabled, False when name unknown."""
        if name not in self._providers:
            return False
        try:
            from bterminal.config import _OPTIONS
            disabled = set(_OPTIONS.get("disabled_providers") or [])
        except Exception:
            disabled = set()
        return name not in disabled

    def names(self) -> list[str]:
        return sorted(self._providers)

    def default_provider(self) -> AIProvider:
        """Return the configured default provider instance.

        Falls back to:
          1. The configured default if it's registered AND enabled.
          2. The first ENABLED provider alphabetically (task #11 — so
             the dropdown's pre-selection always points at something
             the user hasn't hidden).
          3. The first registered provider (legacy fallback for the
             rare case where ALL providers are disabled — caller
             surfaces an error).
          4. RuntimeError if nothing is registered.
        """
        if (self._default_name in self._providers
                and self.is_enabled(self._default_name)):
            return self._providers[self._default_name]
        enabled_list = self.enabled()
        if enabled_list:
            return enabled_list[0]
        if self._providers:
            return self.all()[0]
        raise RuntimeError("No providers registered")


# ─── Module-level singleton ──────────────────────────────────────────────────

_registry: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    """Return the lazy-loaded module-level ProviderRegistry singleton.

    First call instantiates from defaults.json + user override (if
    `~/.config/bterminal/providers.json` exists). Subsequent calls
    return the same instance. Use `reset_registry()` in tests for a
    clean slate.
    """
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_registry() -> None:
    """Drop the cached registry — next get_registry() rebuilds.

    Intended for tests; production code should not need this."""
    global _registry
    _registry = None


def register_provider_class(name: str, cls: type[AIProvider]) -> None:
    """Register a new provider class. Future calls to ProviderRegistry()
    will instantiate it for any matching entry in providers.json.

    Used by future tasks that add providers (T5+ Aider, etc.) without
    editing this module. Not required for the bundled claude/copilot
    pair, which are mapped statically above.
    """
    _PROVIDER_CLASSES[name] = cls


__all__ = [
    "AIProvider",
    "ClaudeProvider",
    "CopilotProvider",
    "DEFAULTS_PATH",
    "ProviderCapabilities",
    "ProviderDisplay",
    "ProviderRegistry",
    "SessionStats",
    "USER_OVERRIDE_PATH",
    "get_registry",
    "load_providers_config",
    "register_provider_class",
    "reset_registry",
]
