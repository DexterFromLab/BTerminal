"""Phase 2 (#86 / #14) — _load_local_errata tolerates corrupted JSON.

Caller pre-corrupts errata.json on disk before invoking. Verifies the
loader returns [] without raising.
"""
from __future__ import annotations

import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, REPO)

from bterminal.updater import _load_local_errata  # noqa: E402

out = _load_local_errata()
assert out == [], f"expected [] on bad JSON, got {out!r}"
print("errata-loader-ok")
