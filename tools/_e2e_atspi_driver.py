#!/usr/bin/env python3
"""tools/_e2e_atspi_driver.py — accessibility-based UI driver for
BTerminal e2e tests.

GTK doesn't have native XPath but AT-SPI exposes the widget tree
with roles + names — close enough. This driver navigates that tree
and clicks widgets BY LABEL, eliminating the flaky-coordinate
problem of xdotool mousemove + click.

Usage (from helper bash):
    python3 tools/_e2e_atspi_driver.py click_menu "Narzędzia"
    python3 tools/_e2e_atspi_driver.py click_menu_item "Narzędzia" "Diagnostyka…"
    python3 tools/_e2e_atspi_driver.py click_button "Zapisz"
    python3 tools/_e2e_atspi_driver.py find_widget --role label --name "Sprawdzaj aktualizacje"

Returns 0 on success, non-zero on failure. Prints widget info to
stdout for caller to log.

Requires:
    - pyatspi (Python AT-SPI bindings)
    - BT running with a11y bridge enabled (default GTK behaviour
      unless GTK_A11Y=none / NO_AT_BRIDGE=1 is set)
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import pyatspi
except ImportError:
    print("ERROR: pyatspi not installed", file=sys.stderr)
    sys.exit(2)


def find_bt_app(timeout=5.0):
    """Find the BTerminal Atspi.Application by name. Polls because
    BT might not have registered with a11y bridge instantly."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        desktop = pyatspi.Registry.getDesktop(0)
        for app in desktop:
            if not app:
                continue
            name = app.name or ""
            if name.lower() in ("bterminal", "python3"):
                # Some apps register as "python3" if the binary is Python
                # — check children for BTerminal window.
                for child in app:
                    if child and "BTerminal" in (child.name or ""):
                        return app
                if "BTerminal" in name:
                    return app
        time.sleep(0.3)
    return None


def walk(node, depth=0, max_depth=20):
    """Yield (node, depth) for every descendant."""
    if depth > max_depth or node is None:
        return
    yield node, depth
    try:
        for child in node:
            if child is None:
                continue
            yield from walk(child, depth + 1, max_depth)
    except Exception:
        pass


def find_first(app, role=None, name=None, name_substr=None):
    """Walk the app tree, return first node matching filters."""
    role_obj = None
    if role:
        # pyatspi role names are uppercase enum values
        role_obj = getattr(pyatspi, f"ROLE_{role.upper()}", None)
        if role_obj is None:
            print(f"unknown role: {role}", file=sys.stderr)
            return None
    for node, _ in walk(app):
        try:
            if role_obj and node.getRole() != role_obj:
                continue
            n = node.name or ""
            if name and n != name:
                continue
            if name_substr and name_substr not in n:
                continue
            return node
        except Exception:
            continue
    return None


def click_action(node):
    """Invoke the 'click' action on a node via Atspi.Action interface."""
    try:
        action = node.queryAction()
    except NotImplementedError:
        print(f"node {node.name!r} has no Action interface", file=sys.stderr)
        return False
    for i in range(action.nActions):
        a_name = action.getName(i).lower()
        if a_name in ("click", "press", "activate"):
            action.doAction(i)
            return True
    # Fallback: just do action 0
    if action.nActions > 0:
        action.doAction(0)
        return True
    return False


def cmd_click_menu(args):
    """Open a top-level menu by label (e.g. 'Narzędzia')."""
    app = find_bt_app()
    if not app:
        print("BT app not found via AT-SPI", file=sys.stderr)
        return 2
    node = find_first(app, role="menu", name=args.label)
    if not node:
        # Try 'menu_item' role too — top-level menubar items often have
        # role MENU_ITEM in some GTK versions
        node = find_first(app, role="menu_item", name=args.label)
    if not node:
        print(f"menu '{args.label}' not found in widget tree", file=sys.stderr)
        return 1
    if click_action(node):
        print(f"OK clicked menu {args.label!r}")
        return 0
    print(f"could not invoke action on {args.label!r}", file=sys.stderr)
    return 1


def cmd_click_menu_item(args):
    """Click a menu item under a parent menu (must be open already, or
    we open it first)."""
    app = find_bt_app()
    if not app:
        return 2
    # First open the parent menu
    parent = find_first(app, role="menu", name=args.menu)
    if parent is None:
        parent = find_first(app, role="menu_item", name=args.menu)
    if parent is None:
        print(f"parent menu {args.menu!r} not found", file=sys.stderr)
        return 1
    click_action(parent)
    time.sleep(0.4)
    # Now find the child item
    item = find_first(app, role="menu_item", name=args.item)
    if item is None:
        print(f"menu item {args.item!r} not found under {args.menu!r}",
              file=sys.stderr)
        return 1
    if click_action(item):
        print(f"OK clicked {args.menu} → {args.item}")
        return 0
    return 1


def cmd_click_button(args):
    app = find_bt_app()
    if not app:
        return 2
    node = find_first(app, role="push_button", name=args.label)
    if not node:
        print(f"button {args.label!r} not found", file=sys.stderr)
        return 1
    if click_action(node):
        print(f"OK clicked button {args.label!r}")
        return 0
    return 1


def cmd_find_widget(args):
    """Diagnostic: find a widget and print its name + role + bounds."""
    app = find_bt_app()
    if not app:
        return 2
    node = find_first(app, role=args.role, name=args.name,
                      name_substr=args.name_substr)
    if not node:
        print("not found")
        return 1
    try:
        comp = node.queryComponent()
        ext = comp.getExtents(pyatspi.DESKTOP_COORDS)
        print(f"name={node.name!r} role={node.getRoleName()} "
              f"bounds=({ext.x},{ext.y},{ext.width},{ext.height})")
    except Exception:
        print(f"name={node.name!r} role={node.getRoleName()} bounds=?")
    return 0


def cmd_dump_tree(args):
    """Diagnostic: print the entire widget tree (depth-limited)."""
    app = find_bt_app()
    if not app:
        return 2
    for node, depth in walk(app, max_depth=args.max_depth):
        try:
            indent = "  " * depth
            print(f"{indent}[{node.getRoleName()}] {node.name!r}")
        except Exception:
            pass
    return 0


def main():
    parser = argparse.ArgumentParser(prog="_e2e_atspi_driver.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_menu = sub.add_parser("click_menu",
                            help="open top-level menu by label")
    p_menu.add_argument("label")
    p_menu.set_defaults(func=cmd_click_menu)

    p_item = sub.add_parser("click_menu_item",
                            help="click <menu> → <item>")
    p_item.add_argument("menu")
    p_item.add_argument("item")
    p_item.set_defaults(func=cmd_click_menu_item)

    p_btn = sub.add_parser("click_button",
                           help="click button by label")
    p_btn.add_argument("label")
    p_btn.set_defaults(func=cmd_click_button)

    p_find = sub.add_parser("find_widget",
                            help="locate widget; print name+role+bounds")
    p_find.add_argument("--role")
    p_find.add_argument("--name")
    p_find.add_argument("--name-substr", dest="name_substr")
    p_find.set_defaults(func=cmd_find_widget)

    p_dump = sub.add_parser("dump_tree",
                            help="print full widget tree")
    p_dump.add_argument("--max-depth", type=int, default=10)
    p_dump.set_defaults(func=cmd_dump_tree)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
