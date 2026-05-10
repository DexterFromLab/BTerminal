"""E2E test for BUG#13 — File menu has Claude-specific item label
'New Claude Code session…' although BT now ships with multiple AI
providers (Claude, Copilot, Aider, Ollama).

User report (manual QA, 2026-05-10): "Nowa sesja Claude Code? Teraz
obsługujemy wiele AI, powinno być Nowa sesja AI". The menu was
authored when Claude was the only AI provider; refactor since (T2.1
provider abstraction) didn't update this label.

Two parts to fix:

1. **Label**: `bterminal/app.py:452` uses
   `N_("New Claude Code session…")`. Should be a generic
   `N_("New AI session…")` so the catalog has one entry covering
   all providers.

2. **Callback**: `lambda: self.sidebar._on_add_claude()` hard-codes
   Claude. Should route through a provider picker (the same
   `Add ▼` dropdown the sidebar already exposes) so the user
   chooses Claude / Copilot / Aider / Ollama at click time.

Test pins both. Visual evidence captured on VM (see
`smoke-logs/bug13-file-menu-generic-ai/`).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_PY = REPO_ROOT / "bterminal" / "app.py"
PL_PO = REPO_ROOT / "locale" / "pl" / "LC_MESSAGES" / "bterminal.po"


def _parse_po_msgs(po_path: Path) -> dict[str, str]:
    if not po_path.is_file():
        return {}
    out = {}
    cur_id = cur_str = None
    in_id = in_str = False
    for raw in po_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            if cur_id is not None:
                out[cur_id] = cur_str or ""
            cur_id = _unquote(line[len("msgid "):])
            cur_str = ""
            in_id, in_str = True, False
        elif line.startswith("msgstr "):
            cur_str = _unquote(line[len("msgstr "):])
            in_id, in_str = False, True
        elif line.startswith('"') and line.endswith('"'):
            chunk = _unquote(line)
            if in_id:
                cur_id = (cur_id or "") + chunk
            elif in_str:
                cur_str = (cur_str or "") + chunk
        elif not line:
            if cur_id is not None:
                out[cur_id] = cur_str or ""
                cur_id = cur_str = None
                in_id = in_str = False
    if cur_id is not None:
        out[cur_id] = cur_str or ""
    return out


def _unquote(s: str) -> str:
    """Strip surrounding quotes + decode gettext escapes; UTF-8 stays as-is."""
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r",
                       '"': '"', "\\": "\\"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _file_menu_block() -> str:
    """Slice the section of app.py:_build_menubar that builds the
    File menu (between '# ── File ─' marker and '# ── View ─'
    marker, or the next major section)."""
    src = APP_PY.read_text(encoding="utf-8")
    start_marker = src.find("# ── File")
    assert start_marker > 0, "could not locate File menu marker"
    end_marker = src.find("# ── View", start_marker)
    if end_marker < 0:
        end_marker = src.find("# ── Tools", start_marker)
    assert end_marker > start_marker
    return src[start_marker:end_marker]


# ── Layer 1: source label is generic, not Claude-specific ───────────────


def test_file_menu_does_not_hardcode_claude_in_label():
    """Pin: the AI-session menu item label must NOT contain
    'Claude Code'. Generic 'New AI session…' (or equivalent) is
    expected so the entry covers all providers."""
    block = _file_menu_block()
    # Find any N_("...") in the file menu block that mentions Claude
    matches = re.findall(r'N_\("([^"]*Claude[^"]*)"\)', block)
    assert not matches, (
        f"File menu label still hard-codes Claude: {matches}. "
        f"Replace with N_('New AI session…') and route to a "
        f"provider picker. Block:\n{block[:500]}"
    )


def test_file_menu_has_generic_new_ai_session_label():
    """Pin: positively, a generic 'New AI session…' (or close)
    must be present. Acceptable forms include:
      - 'New AI session…'
      - 'New AI tab…'
      - 'Add AI session…'
    """
    block = _file_menu_block()
    candidates = [
        r'N_\("New AI session…"\)',
        r'N_\("New AI session\.\.\."\)',
        r'N_\("New AI tab…"\)',
        r'N_\("Add AI session…"\)',
    ]
    if not any(re.search(c, block) for c in candidates):
        pytest.fail(
            "File menu lacks a generic AI-session item. Add one of:\n"
            "  - file_menu.append(_item(N_('New AI session…'), …))\n"
            "Block:\n" + block[:500]
        )


def test_file_menu_callback_does_not_hardcode_on_add_claude():
    """Pin: the AI-session callback must NOT route directly to
    `_on_add_claude` (Claude-only). Use a provider-picker
    callback like `_on_add_ai_session` that opens a chooser."""
    block = _file_menu_block()
    # Find the lambda or callback for the AI-session item — match
    # any line that has both N_(...AI...) and a callback
    bad = re.search(
        r'_item\(\s*N_\("[^"]*"\),\s*lambda[^)]*_on_add_claude',
        block, re.DOTALL,
    )
    assert not bad, (
        f"File menu's AI-session item still calls "
        f"`_on_add_claude` directly. Refactor to call a "
        f"provider-picker (e.g. `_on_add_ai_session`)."
    )

    # Negative: claude-only callback must not be referenced from
    # any new generic label
    new_label_ref = re.search(
        r'N_\("New AI[^"]*"\),\s*lambda[^)]*_on_add_claude',
        block, re.DOTALL,
    )
    assert not new_label_ref, (
        f"a generic 'New AI session…' label was added but the "
        f"callback still hardcodes _on_add_claude. Use a provider "
        f"picker instead."
    )


# ── Layer 2: PL translation present for the new msgid ───────────────────


def test_pl_catalog_translates_new_ai_session_msgid():
    """Pin: once the source switches to the generic msgid, the PL
    catalog must carry a translation. Skipped while the source
    still has Claude-specific label — Layer 1 fix must come first."""
    block = _file_menu_block()
    if 'N_("New AI session' not in block \
       and 'N_("New AI tab' not in block \
       and 'N_("Add AI session' not in block:
        pytest.skip("source still on Claude-specific label; "
                    "Layer 1 fix needed before this test activates")

    po = _parse_po_msgs(PL_PO)
    candidates = ["New AI session…", "New AI tab…", "Add AI session…"]
    found = [c for c in candidates if c in po and po[c].strip()]
    assert found, (
        f"none of {candidates} have non-empty PL msgstr in "
        f"{PL_PO.name}. Run extract+update+fill+compile."
    )


def test_existing_claude_specific_msgstr_can_be_removed_after_fix():
    """Bookkeeping pin: the catalog currently has 'New Claude Code
    session…' translated to 'Nowa sesja Claude Code…'. After the
    refactor, this msgid will become orphan (no source reference).
    `./tools/i18n.sh update` will mark it `#~ msgid` (commented
    out). Test asserts that AT LEAST today the translation exists
    so we know what we're replacing."""
    po = _parse_po_msgs(PL_PO)
    msgstr = po.get("New Claude Code session…", "").strip()
    if not msgstr:
        pytest.skip("Claude-specific msgid already gone — fix landed")
    assert "Claude" in msgstr, (
        f"unexpected: msgstr exists but doesn't mention Claude: "
        f"{msgstr!r}"
    )
