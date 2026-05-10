"""Shared helpers for tests that spawn BTerminal as a subprocess.

Why: bterminal/__main__.py shows a modal GTK license dialog on first
launch (defaults/license/LICENSE.<lang>.md). The dialog blocks the
main loop, which means debug-REST never starts and tests time out
waiting for /api/health. Each fixture that creates a fresh isolated
HOME must pre-seed an accepted license hash in options.json before
spawning the subprocess.

Usage:
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(...)
    seed_license(home)  # call BEFORE subprocess.Popen([..., "-m", "bterminal"])
    subprocess.Popen(["xvfb-run", ..., env={"HOME": home, ...}])
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
LICENSE_PATH = REPO_ROOT / "defaults" / "license" / "LICENSE.en.md"


def seed_license(home: str | os.PathLike) -> None:
    """Pre-accept the license in `home/.config/bterminal/options.json`.

    Uses LICENSE.en.md (the universal fallback that bterminal.license
    falls back to when the requested locale is missing). Forces
    `language: "en"` so the license module reads exactly the file we
    hashed — locale-specific files would have a different hash.

    Idempotent: if the options file already exists, the new keys are
    merged in without clobbering unrelated entries.
    """
    home_path = Path(home)
    options_dir = home_path / ".config" / "bterminal"
    options_dir.mkdir(parents=True, exist_ok=True)
    options_file = options_dir / "options.json"

    license_hash = hashlib.sha256(LICENSE_PATH.read_bytes()).hexdigest()

    existing: dict = {}
    if options_file.exists():
        try:
            existing = json.loads(options_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}

    existing.update({
        "language": "en",
        "license_accepted_hash": license_hash,
        "license_accepted_at": "2026-01-01T00:00:00",
    })
    options_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
