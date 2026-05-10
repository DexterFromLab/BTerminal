"""Forward compat: plugin-provided 4th provider
(#50 / #122, audit § 6.5 #23).

A plugin can ship its own AIProvider subclass and register it
via `ProviderRegistry.register(provider)`. This is the documented
extension point — a plugin without a defaults.json entry can
still be a first-class provider for dispatch purposes.

Three decision branches:
  (a) Capability flags fully True (rules_inject + task_auto_trigger
      + stats_bar all True) — all dispatchers handle the plugin.
  (b) Partial caps (rules_inject True, task_auto_trigger False) —
      rules-inject dispatch fires, auto-trigger dispatch skips.
  (c) Unknown reader class — plugin has stats_bar=True but no
      entry in `bterminal.ui.stats._READER_CLASSES` → factory
      returns None, widget code skips the SessionStatsBar mount
      gracefully.

Pinned defenses:
  - `should_inject_rules` and `should_run_auto_trigger` (in
    `bterminal.ui.terminal_tab`) read provider via `registry.get
    (name)` — purely capability-driven, no defaults.json
    requirement.
  - `create_stats_reader_for_ai_config` checks the reader
    registry independently of capabilities — capability says "the
    provider WANTS a stats bar", reader registry says "I know how
    to read this provider's logs."
  - `ensure_context_files_for_all_providers` iterates
    `registry.names()` — picks up plugins automatically.
  - `_format_image_paste_for_provider` reads `provider._argv_spec`
    — plugin-provided spec respected.

Manual VM smoke (load plugin via plugins dir, spawn tab) is
documented in tests/manual/README.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.base import (
    AIProvider,
    ProviderCapabilities,
    ProviderDisplay,
    SessionStats,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── Plugin provider builders (representative stubs) ────────────────────


class _PluginProviderBase(AIProvider):
    """Skeleton plugin provider for tests. Subclasses tweak
    capabilities via class attributes."""

    name = "plugin-cli-test"
    _CAPS_OVERRIDE: dict = {}
    _ARGV_SPEC: dict = {}

    def __init__(self):
        self.display = ProviderDisplay(
            icon="🔌",
            short_label="Plugin",
            long_label="Plugin CLI (test stub)",
            color="#00ffff",
        )
        # Default caps for the FULL scenario; subclasses override
        defaults = {
            "intro_prompt": True,
            "rules_inject": True,
            "task_auto_trigger": True,
            "stats_bar": True,
            "session_log": False,
            "skip_permissions": False,
            "context_file": None,
        }
        defaults.update(self._CAPS_OVERRIDE)
        self.capabilities = ProviderCapabilities(**defaults)
        self.pricing = {}
        self._argv_spec = self._ARGV_SPEC
        self._binary_spec = {"binary": "/tmp/plugin-bin",
                              "search_paths": [],
                              "argv_prefix": []}

    def find_binary(self):
        return self._binary_spec.get("binary")

    def build_argv(self, config: dict, intro_prompt: str) -> list[str]:
        return [self._binary_spec["binary"], "--mode", "stub"]

    def session_log_glob(self, project_dir: str):
        return None

    def parse_session_stats(self, log_path: str) -> SessionStats:
        return SessionStats()


class FullPluginProvider(_PluginProviderBase):
    """(a) All caps True — full participation in dispatch."""
    name = "plugin-cli-full"


class PartialCapPluginProvider(_PluginProviderBase):
    """(b) rules_inject=True but task_auto_trigger=False."""
    name = "plugin-cli-partial"
    _CAPS_OVERRIDE = {
        "rules_inject": True,
        "task_auto_trigger": False,
        "stats_bar": False,
    }


class StatsBarOnlyPluginProvider(_PluginProviderBase):
    """(c) stats_bar=True but no entry in _READER_CLASSES → factory
    returns None gracefully."""
    name = "plugin-cli-stats-only"
    _CAPS_OVERRIDE = {
        "rules_inject": False,
        "task_auto_trigger": False,
        "stats_bar": True,  # claims stats but no reader registered
    }


# ─── ProviderRegistry.register() picks up plugins ───────────────────────


def test_registry_register_adds_plugin_provider():
    """Pin: `registry.register(plugin)` adds the provider to the
    in-memory dict. Subsequent `get(plugin.name)` returns it."""
    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    plugin = FullPluginProvider()
    reg.register(plugin)

    assert reg.has("plugin-cli-full")
    assert reg.get("plugin-cli-full") is plugin


def test_registry_register_does_not_evict_bundled_providers():
    """Adding a plugin doesn't unregister the 3 bundled providers
    (claude/copilot/aider). Pin so a future register() refactor
    that mutates _providers can't accidentally truncate."""
    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(FullPluginProvider())

    for bundled in ("claude", "copilot", "aider"):
        assert reg.has(bundled), (
            f"bundled provider {bundled!r} evicted by register()"
        )


def test_registry_names_includes_plugin_after_register():
    """`registry.names()` returns the union of bundled + plugins.
    `ensure_context_files_for_all_providers` iterates this list,
    so plugins with `context_file` get auto-mirroring."""
    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(FullPluginProvider())
    names = reg.names()
    assert "plugin-cli-full" in names
    assert {"claude", "copilot", "aider"}.issubset(set(names))


def test_register_overrides_provider_with_same_name():
    """`register()` replaces existing entry under the same name.
    Pin: a plugin can intentionally override a bundled provider
    (advanced use case — mostly we don't, but the API allows it)."""
    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    fake_aider = FullPluginProvider()
    fake_aider.name = "aider"  # collide with bundled
    reg.register(fake_aider)
    # The bundled aider is replaced
    assert reg.get("aider") is fake_aider


# ─── Branch (a): Full caps — all dispatchers handle plugin ──────────────


def test_full_plugin_should_inject_rules_returns_true():
    """`should_inject_rules` with plugin's ai_config — rules_inject
    cap True → returns True."""
    from bterminal.ui.terminal_tab import should_inject_rules

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(FullPluginProvider())

    ai_cfg = {"provider": "plugin-cli-full",
              "name": "MyPluginSession",
              "project_dir": "/tmp/p"}
    assert should_inject_rules(ai_cfg, reg) is True


def test_full_plugin_should_run_auto_trigger_returns_true():
    """`should_run_auto_trigger` — task_auto_trigger cap True →
    True. Plugin's auto-trigger fires identically to bundled."""
    from bterminal.ui.terminal_tab import should_run_auto_trigger

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(FullPluginProvider())

    ai_cfg = {"provider": "plugin-cli-full",
              "name": "MyPluginSession",
              "project_dir": "/tmp/p"}
    assert should_run_auto_trigger(ai_cfg, reg) is True


def test_full_plugin_extract_rules_inject_bytes_works():
    """`extract_rules_inject_bytes` is provider-agnostic
    (#93 contract). Pin: it works for plugin provider too."""
    from bterminal.ui.terminal_tab import extract_rules_inject_bytes

    out = extract_rules_inject_bytes(
        "plugin-cli-full", "myproj", "## Plugin rules\n- be terse")
    assert isinstance(out, bytes)
    assert b"Plugin rules" in out
    # Byte-identical to other providers (provider-agnostic)
    claude_out = extract_rules_inject_bytes(
        "claude", "myproj", "## Plugin rules\n- be terse")
    assert out == claude_out


def test_full_plugin_format_image_paste_uses_plugin_argv_spec():
    """`_format_image_paste_for_provider` reads
    `provider._argv_spec` — plugin's spec is respected. Pin via
    a plugin with a custom image_paste_template."""
    from bterminal.ui.terminal_tab import TerminalTab

    class PluginWithCustomPaste(FullPluginProvider):
        name = "plugin-with-paste"
        _ARGV_SPEC = {
            "image_paste_template": "Plugin saw image: {path} — describe."
        }

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(PluginWithCustomPaste())

    # Stub a TerminalTab with the plugin's ai_config
    tab = MagicMock(spec=TerminalTab)
    tab.ai_config = {"provider": "plugin-with-paste", "name": "x"}
    tab._format_image_paste_for_provider = (
        TerminalTab._format_image_paste_for_provider.__get__(tab)
    )

    # Patch get_registry to return our test registry
    from unittest.mock import patch as _patch
    with _patch("bterminal.providers.get_registry",
                 return_value=reg):
        out = tab._format_image_paste_for_provider("/tmp/img.png")
    assert "Plugin saw image" in out
    assert "/tmp/img.png" in out


# ─── Branch (b): Partial caps — only rules-inject fires ─────────────────


def test_partial_plugin_should_inject_rules_returns_true():
    """rules_inject cap True for partial plugin → True."""
    from bterminal.ui.terminal_tab import should_inject_rules

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(PartialCapPluginProvider())

    ai_cfg = {"provider": "plugin-cli-partial",
              "name": "MyPartial",
              "project_dir": "/tmp/p"}
    assert should_inject_rules(ai_cfg, reg) is True


def test_partial_plugin_should_run_auto_trigger_returns_false():
    """task_auto_trigger cap False → False. Plugin OPTS OUT of
    auto-trigger. Important: partial caps allow plugins to pick
    which dispatch flows they participate in."""
    from bterminal.ui.terminal_tab import should_run_auto_trigger

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(PartialCapPluginProvider())

    ai_cfg = {"provider": "plugin-cli-partial",
              "name": "MyPartial",
              "project_dir": "/tmp/p"}
    assert should_run_auto_trigger(ai_cfg, reg) is False


def test_partial_plugin_stats_bar_options_skip_when_off():
    """`stats_widget_options_for_ai_config` returns hide flags
    based on capability. When the partial plugin has
    stats_bar=False, options are returned but factory creates
    no reader (next test). Pin: options resolution doesn't care
    about stats_bar=False."""
    from bterminal.ui.stats import stats_widget_options_for_ai_config

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(PartialCapPluginProvider())

    ai_cfg = {"provider": "plugin-cli-partial", "project_dir": "/tmp/p"}
    opts = stats_widget_options_for_ai_config(ai_cfg, reg)
    assert isinstance(opts, dict)
    # stats_bar_no_plan_usage default False → hide_plan_usage False
    assert opts.get("hide_plan_usage") is False


# ─── Branch (c): stats_bar=True but no _READER_CLASSES entry ────────────


def test_stats_only_plugin_factory_returns_none_without_reader():
    """`create_stats_reader_for_ai_config` checks
    `_READER_CLASSES` independently of `capabilities.stats_bar`.
    Plugin has stats_bar=True but the reader registry only knows
    aider/claude/copilot — factory returns None.

    Effect: TerminalTab spawns the plugin's tab WITHOUT a
    SessionStatsBar. The user sees no stats — graceful, not
    a crash."""
    from bterminal.ui.stats import create_stats_reader_for_ai_config

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(StatsBarOnlyPluginProvider())

    ai_cfg = {"provider": "plugin-cli-stats-only",
              "name": "MyPlugin",
              "project_dir": "/tmp/p"}
    reader = create_stats_reader_for_ai_config(ai_cfg, reg)
    assert reader is None, (
        f"factory unexpectedly returned a reader for unknown plugin: "
        f"{reader!r}"
    )


def test_stats_only_plugin_does_not_break_terminal_tab_init_flow():
    """Source-grep: TerminalTab.__init__ checks `if reader is not
    None:` before mounting SessionStatsBar. Pin so a plugin with
    stats_bar=True but no reader doesn't NPE in the tab init."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
           ).read_text()
    init_idx = src.find("def __init__(self,")
    init_end = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:init_end]
    # The reader-None guard is in place
    assert "if reader is not None:" in init_body, (
        "TerminalTab no longer guards stats_bar mount on reader "
        "presence — plugin without reader would NPE"
    )


# ─── Capability dispatch helpers handle unknown provider gracefully ────


def test_unknown_provider_in_ai_config_returns_false():
    """Same defensive contract for plugin path: if a saved
    ai_config names a provider that's NOT registered (plugin
    uninstalled?), dispatch returns False. Mirrors #19's
    `dispatch_helpers_skip_unknown_provider` for the bundled
    providers."""
    from bterminal.ui.terminal_tab import (
        should_inject_rules, should_run_auto_trigger,
    )
    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    # Don't register any plugin
    ai_cfg = {"provider": "plugin-not-loaded", "name": "x"}
    assert should_inject_rules(ai_cfg, reg) is False
    assert should_run_auto_trigger(ai_cfg, reg) is False


def test_unknown_provider_factory_returns_none():
    """`create_stats_reader_for_ai_config` for unregistered
    provider → None (KeyError caught, fallback). Pin so a saved
    config naming an uninstalled plugin doesn't crash."""
    from bterminal.ui.stats import create_stats_reader_for_ai_config

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    ai_cfg = {"provider": "plugin-not-loaded", "name": "x",
              "project_dir": "/tmp/p"}
    reader = create_stats_reader_for_ai_config(ai_cfg, reg)
    assert reader is None


# ─── ensure_context_files_for_all_providers iterates plugins ────────────


def test_dispatcher_calls_plugin_context_file_when_capability_present(
        tmp_path):
    """`ensure_context_files_for_all_providers` reads
    `registry.names()` then `provider.capabilities.context_file`.
    A plugin with context_file set gets its file mirrored
    automatically."""
    from bterminal.ctx.helpers import (
        ensure_context_files_for_all_providers,
    )

    class PluginWithContextFile(FullPluginProvider):
        name = "plugin-with-ctx"
        _CAPS_OVERRIDE = {"context_file": "PLUGIN.md"}

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(PluginWithContextFile())

    # Need to monkeypatch get_registry so the helper uses our reg
    from unittest.mock import patch as _patch
    (tmp_path / "CLAUDE.md").write_text("# context")
    with _patch("bterminal.providers.get_registry",
                 return_value=reg):
        results = ensure_context_files_for_all_providers(tmp_path)

    # Plugin's context_file mirrored
    assert results.get("PLUGIN.md") == "symlink"
    assert (tmp_path / "PLUGIN.md").is_symlink()


def test_dispatcher_skips_plugin_without_context_file(tmp_path):
    """A plugin with `context_file=None` (default) is skipped by
    the dispatcher — no entry in results dict for that filename."""
    from bterminal.ctx.helpers import (
        ensure_context_files_for_all_providers,
    )

    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)
    reg.register(FullPluginProvider())  # context_file=None

    from unittest.mock import patch as _patch
    (tmp_path / "CLAUDE.md").write_text("# c")
    with _patch("bterminal.providers.get_registry",
                 return_value=reg):
        results = ensure_context_files_for_all_providers(tmp_path)

    # 3 bundled (CLAUDE.md=self + AGENTS.md + AIDER.md) — plugin
    # with no context_file isn't in the results
    assert all(fn != "plugin" for fn in results.keys())
    assert "CLAUDE.md" in results  # bundled
    assert "AGENTS.md" in results
    assert "AIDER.md" in results


# ─── REST surface dispatches plugin name correctly ──────────────────────


def test_rest_route_signature_accepts_arbitrary_provider_name():
    """`/api/tabs/ai/(?P<provider>[\\w-]+)` regex accepts any
    word-or-dash provider name. Pin so plugin names like
    `plugin-cli-test` route correctly (not just bundled trio)."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    # The route regex
    assert "/api/tabs/ai/(?P<provider>[\\w-]+)" in src or \
        '/api/tabs/ai/(?P<provider>[\\\\w-]+)' in src


def test_route_post_tabs_ai_does_not_hardcode_provider_names():
    """Source-grep: the dispatch handler `_route_post_tabs_ai`
    doesn't have an enum / set check restricting providers to
    {claude, copilot, aider}. Plugin names route through the
    same code path."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    fn_start = src.find("def _route_post_tabs_ai")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    # No exhaustive provider whitelist
    forbidden_patterns = [
        '{"claude", "copilot", "aider"}',
        '["claude", "copilot", "aider"]',
        '("claude", "copilot", "aider")',
    ]
    for pat in forbidden_patterns:
        assert pat not in body, (
            f"_route_post_tabs_ai whitelists providers: {pat!r} — "
            f"plugins can't use the REST endpoint"
        )


# ─── PluginProvider must subclass AIProvider (formal contract) ──────────


def test_plugin_provider_must_subclass_ai_provider():
    """Pin: register() type-hints `AIProvider`. Pass anything else
    and the type-checker would catch it. Test runs at runtime
    confirming inheritance via isinstance."""
    plugin = FullPluginProvider()
    assert isinstance(plugin, AIProvider)
    # And implements the abstract methods
    assert plugin.find_binary() is not None
    assert plugin.build_argv({}, "")


def test_plugin_provider_capabilities_must_be_provider_capabilities():
    """Pin: `provider.capabilities` is a `ProviderCapabilities`
    dataclass. Dispatch helpers (`should_inject_rules` etc.)
    read attributes on this object — duck-typing would work but
    pin the formal type."""
    plugin = FullPluginProvider()
    assert isinstance(plugin.capabilities, ProviderCapabilities)


# ─── Lifecycle: plugin can be re-registered (e.g. plugin reload) ────────


def test_plugin_re_registration_replaces_old_instance():
    """A plugin reload replaces the old instance. Pin so an
    in-app refresh-plugins flow doesn't accumulate stale
    instances."""
    cfg = load_providers_config()
    reg = ProviderRegistry(config=cfg)

    plugin_v1 = FullPluginProvider()
    reg.register(plugin_v1)
    assert reg.get("plugin-cli-full") is plugin_v1

    plugin_v2 = FullPluginProvider()
    reg.register(plugin_v2)
    assert reg.get("plugin-cli-full") is plugin_v2
    # OLD instance gone
    assert reg.get("plugin-cli-full") is not plugin_v1


# ─── Cross-cutting: plugin doesn't break bundled providers' tests ──────


def test_bundled_provider_dispatch_unaffected_by_plugin_registration():
    """Adding a plugin does NOT change behavior for
    claude/copilot/aider dispatches. Pin parity isolation."""
    from bterminal.ui.terminal_tab import (
        should_inject_rules, should_run_auto_trigger,
    )

    cfg = load_providers_config()
    reg_alone = ProviderRegistry(config=cfg)

    reg_with_plugin = ProviderRegistry(config=cfg)
    reg_with_plugin.register(FullPluginProvider())

    aider_cfg = {"provider": "aider", "name": "x",
                 "project_dir": "/tmp/p"}
    # Same behavior regardless of plugin presence
    assert (should_inject_rules(aider_cfg, reg_alone)
            == should_inject_rules(aider_cfg, reg_with_plugin))
    assert (should_run_auto_trigger(aider_cfg, reg_alone)
            == should_run_auto_trigger(aider_cfg, reg_with_plugin))


# ─── Migration markers ─────────────────────────────────────────────────


def test_no_plugin_provider_class_in_bundled_providers_module():
    """Pin: `bterminal/providers/__init__.py` has _PROVIDER_CLASSES
    dict with the 3 bundled. No 'plugin'/'PluginProvider' baked in.
    Plugins live in user plugins dir, registered via
    `registry.register()`."""
    src = (REPO_ROOT / "bterminal" / "providers" / "__init__.py"
           ).read_text()
    assert "PluginProvider" not in src
    # Bundled 3
    assert '"claude":' in src
    assert '"copilot":' in src
    assert '"aider":' in src


def test_register_method_documented_for_plugin_authors():
    """Pin: `register()` has a docstring explaining its role —
    so plugin authors find the integration point."""
    src = (REPO_ROOT / "bterminal" / "providers" / "__init__.py"
           ).read_text()
    fn_idx = src.find("def register(self,")
    next_def = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:next_def]
    # Has SOME docstring
    assert '"""' in body
