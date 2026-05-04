#!/usr/bin/env python3
"""Visual UI demo — patrz na ekran, BTerminal pracuje.

Usage (na VM z DISPLAY=:0 + odpalonym BTerminalem --debug-rest):
  python3 tools/visual_demo.py [--port 7780]

Robi:
  1. Cyklicznie klika każdą zakładkę sidebara (1.5s pauza między)
  2. Otwiera Claude tab "test" przez REST
  3. Pisze 'echo hello world' przez xdotool do terminala
  4. Lower inject_every=2, simulate 2 prompts, force_idle
  5. Rules block wstrzyknięty do terminala — WIDOCZNY
  6. Screenshot na końcu + verify w /api/debug/feed_log

Wymaga: xdotool, BTerminal --debug-rest, X display.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def banner(msg):
    print(f"\n{'═' * 64}")
    print(f"  {msg}")
    print(f"{'═' * 64}")


def step(msg, pause=0.3):
    print(f"  → {msg}")
    time.sleep(pause)


def api_get(base, token, path, **params):
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{base}{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def api_post(base, token, path, body=None):
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    data = json.dumps(body or {}).encode() if body else b""
    req = urllib.request.Request(f"{base}{path}", data=data,
                                  headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def xdotool(*args, check=False):
    env = {**os.environ}
    if "DISPLAY" not in env:
        env["DISPLAY"] = ":0"
    r = subprocess.run(["xdotool", *args], capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        print(f"  ⚠ xdotool {args} failed: {r.stderr.strip()}")
    return r


def find_window():
    r = xdotool("search", "--name", "BTerminal")
    if not r.stdout.strip():
        return None
    return r.stdout.strip().split("\n")[0]


def screenshot(base, token, label):
    try:
        info = api_get(base, token, "/api/window/screenshot")
        target = f"/tmp/demo_{label}.png"
        subprocess.run(["cp", info["path"], target])
        print(f"     📸 {target}  ({info['width']}x{info['height']})")
        return target
    except Exception as exc:
        print(f"     ⚠ screenshot failed: {exc}")
        return None


def setup_rules_config(project, inject_every=2, refresh_every=4):
    """Ustaw inject_every dla projektu w ctx DB."""
    db_path = Path.home() / ".claude-context" / "context.db"
    if not db_path.exists():
        print(f"  ⚠ ctx DB not found at {db_path}")
        return False
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rules_config (
            project TEXT PRIMARY KEY,
            inject_every INTEGER DEFAULT 100,
            refresh_every INTEGER DEFAULT 200
        )
    """)
    conn.execute(
        "INSERT INTO rules_config (project, inject_every, refresh_every) "
        "VALUES (?, ?, ?) ON CONFLICT(project) DO UPDATE SET "
        "inject_every=excluded.inject_every, "
        "refresh_every=excluded.refresh_every",
        (project, inject_every, refresh_every),
    )
    conn.commit()
    conn.close()
    print(f"     rules_config[{project}]: inject_every={inject_every}, refresh_every={refresh_every}")
    return True


def setup_minimal_rules(project):
    """Stwórz minimalny rules.txt dla projektu żeby _fetch_rules_block coś zwrócił."""
    rules_dir = Path.home() / ".config" / "bterminal" / "memory" / project
    rules_dir.mkdir(parents=True, exist_ok=True)
    rules_file = rules_dir / "rules.txt"
    rules_file.write_text(
        "# Demo rules\n"
        "1. To jest reguła demo wstrzyknięta przez visual_demo.py\n"
        "2. Po 2 promptach masz to widzieć w terminalu\n"
        "3. Drugi rule do testu\n"
    )
    print(f"     wrote rules → {rules_file}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="7780")
    parser.add_argument("--session", default="test",
                        help="Claude session name to open")
    parser.add_argument("--project", default="simple_test",
                        help="ctx project name (must match session.project_dir basename)")
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    token_file = Path.home() / ".config" / "bterminal" / "debug_token"
    if not token_file.exists():
        print(f"✗ {token_file} missing — czy BTerminal działa z --debug-rest?")
        sys.exit(1)
    token = token_file.read_text().strip()

    # Verify alive
    try:
        h = api_get(base, token, "/api/health")
        print(f"✓ BTerminal alive (idle {h.get('idle_seconds', 0)}s)")
    except Exception as exc:
        print(f"✗ BTerminal not responding on {base}: {exc}")
        sys.exit(1)

    banner("[1] Sidebar panels — klikam każdą zakładkę")
    panels = ["tasks", "ctx", "memory", "skills", "files", "plugins", "consult", "sessions"]
    for p in panels:
        step(f"sidebar/show '{p}'", pause=0.2)
        api_post(base, token, "/api/window/sidebar/show", {"name": p})
        time.sleep(1.3)  # PATRZ NA EKRAN — panel się zmienia
    screenshot(base, token, "01_panels_done")

    banner(f"[2] Otwórz Claude session '{args.session}'")
    try:
        r = api_post(base, token, "/api/tabs/claude", {"config_name": args.session})
        idx = r["idx"]
        print(f"  ✓ tab idx={idx}")
        time.sleep(2)
        screenshot(base, token, "02_claude_opened")
    except Exception as exc:
        print(f"  ✗ open failed: {exc}")
        print(f"     skipping rest of demo (potrzebujesz seed'd Claude session)")
        return

    banner("[3] Setup rules + lower inject_every")
    step("create demo rules.txt for project")
    setup_minimal_rules(args.project)
    step("set inject_every=2 in rules_config")
    setup_rules_config(args.project, inject_every=2, refresh_every=4)

    banner("[4] xdotool — wpisuję 'echo demo' do terminala")
    wid = find_window()
    if wid:
        step(f"focus window {wid}")
        xdotool("windowactivate", wid)
        time.sleep(0.5)
        step("type 'echo demo'")
        xdotool("type", "--delay", "70", "echo demo from xdotool")
        time.sleep(1.5)
        screenshot(base, token, "03_typed_in_terminal")
    else:
        print("  ⚠ BTerminal window not found — skip xdotool step")

    banner("[5] Symuluję 2 prompty → boundary inject_every=2 → _inject_pending set")
    for i in range(1, 3):
        step(f"simulate_prompt #{i}", pause=0.3)
        api_post(base, token, f"/api/tabs/{idx}/simulate_prompt")
        time.sleep(0.8)

    banner("[6] force_idle → wykonuje pending inject_rules → bytes do VTE")
    step("force_idle — fires _do_inject_rules", pause=0.3)
    api_post(base, token, f"/api/tabs/{idx}/force_idle")
    time.sleep(2.5)  # PATRZ — rules block powinien pojawić się w terminalu
    screenshot(base, token, "04_after_rules_inject")

    banner("[7] Verify — feed_log capture")
    events = api_get(base, token, "/api/debug/feed_log", label="rules_inject")
    rules_events = events.get("events", [])
    print(f"  rules_inject events captured: {len(rules_events)}")
    if rules_events:
        import base64
        last = rules_events[-1]
        decoded = base64.b64decode(last["bytes_b64"]).decode("utf-8", errors="replace")
        print(f"  last event preview ({len(decoded)} chars):")
        for line in decoded.split("\n")[:5]:
            print(f"     │ {line[:100]}")
    else:
        print("  ⚠ no rules_inject events — sprawdź czy rules.txt nie jest pusty")

    banner("DEMO ZAKOŃCZONE")
    print(f"  screenshots: ls /tmp/demo_*.png")
    print(f"  feed_log: curl -H 'Authorization: Bearer $TOKEN' {base}/api/debug/feed_log | jq")


if __name__ == "__main__":
    main()
