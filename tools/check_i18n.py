#!/usr/bin/env python3
"""i18n audit: scan bterminal/ for hardcoded Polish strings.

Reports user-facing string literals that contain Polish diacritics but
are NOT wrapped in `_()`, `ngettext()` or `N_()` markers. Skips:

  - lines containing only comments
  - module / function / class docstrings (the leading string literal)
  - string literals that are arguments of `_()`, `ngettext()` or `N_()`
  - string literals INSIDE such an argument (e.g. concatenated parts)

Designed as a pre-commit check. Exits 1 (with a file:line:value report)
when any unmarked Polish string is found in the bterminal/ package.

Note: this script does NOT scan tools/, defaults/, errata.json, or
README.md — those are intentionally English-only per project policy
and any Polish there is a content bug separate from i18n marker drift.

Usage:
    ./tools/check_i18n.py             # scan bterminal/
    ./tools/check_i18n.py path/to.py  # scan one file
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PL_RE = re.compile(r"[ąęłńóśźż"
                   r"ĄĘŁŃÓŚŹŻ]")
EXEMPT_FUNCS = frozenset({"_", "ngettext", "N_"})

# bterminal/ is the only tree we scan. Tools and defaults are English-only
# by policy — any PL there is a separate content bug.
DEFAULT_ROOTS = ["bterminal"]


def _attach_parents(tree: ast.AST) -> None:
    """Walk tree, set node._parent for upward traversal."""
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]


def _is_docstring(node: ast.Constant) -> bool:
    """Return True iff `node` is the leading docstring of a Module /
    FunctionDef / AsyncFunctionDef / ClassDef body."""
    parent = getattr(node, "_parent", None)
    if not isinstance(parent, ast.Expr):
        return False
    grandparent = getattr(parent, "_parent", None)
    if not isinstance(
        grandparent,
        (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
    ):
        return False
    return bool(grandparent.body) and grandparent.body[0] is parent


def _is_under_translation_call(node: ast.AST) -> bool:
    """Walk upward from `node`. Return True if any ancestor is a Call
    whose function is one of EXEMPT_FUNCS (`_`, `ngettext`, `N_`)."""
    cur = getattr(node, "_parent", None)
    while cur is not None:
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name):
            if cur.func.id in EXEMPT_FUNCS:
                return True
        cur = getattr(cur, "_parent", None)
    return False


def audit_file(path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, value-prefix) for unmarked PL strings."""
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []
    _attach_parents(tree)

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        if not PL_RE.search(node.value):
            continue
        if _is_docstring(node):
            continue
        if _is_under_translation_call(node):
            continue
        snippet = node.value.replace("\n", " ").strip()
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."
        violations.append((node.lineno, snippet))
    return violations


def main(argv: list[str]) -> int:
    if argv:
        targets = [Path(a) for a in argv]
    else:
        repo_root = Path(__file__).resolve().parent.parent
        targets = [repo_root / r for r in DEFAULT_ROOTS]

    py_files: list[Path] = []
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            py_files.append(target)
        elif target.is_dir():
            py_files.extend(sorted(target.rglob("*.py")))

    total_violations = 0
    for f in py_files:
        violations = audit_file(f)
        for lineno, snippet in violations:
            print(f"{f}:{lineno}: unmarked PL string: {snippet!r}")
            total_violations += 1

    if total_violations:
        print()
        print(f"✗ {total_violations} unmarked Polish string(s) found.")
        print("  Wrap each with _() / ngettext() / N_() from bterminal.i18n,")
        print("  or convert the message to English if it is sent to an AI prompt.")
        return 1
    print(f"✓ i18n audit clean ({len(py_files)} file(s) scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
