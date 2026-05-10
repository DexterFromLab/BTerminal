"""Phase 5 (#86 / #14) — _remote_license_blob_path resolves to a real
markdown blob, not a symlink target string.

Catches the deeper variant of #52: even with `git show` the right
language-specific blob must exist in the tree.
"""
from __future__ import annotations

import os
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, REPO)

from bterminal.updater import _remote_license_blob_path  # noqa: E402

path = _remote_license_blob_path()
assert path == "defaults/license/LICENSE.en.md", f"wrong default path: {path!r}"
print("blob-path-ok")

target = os.path.join(REPO, path)
assert os.path.isfile(target), f"blob target missing: {target}"
text = open(target, encoding="utf-8").read()
assert len(text) > 100 and text.lstrip().startswith("#"), (
    f"blob is not markdown: {text[:80]!r}"
)
print("blob-content-ok")
