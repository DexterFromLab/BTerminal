"""Phase 1 (#86 / #14) — _read_local_license returns markdown TEXT.

Regression for #52 where the dialog showed a path string like
'defaults/license/LICENSE.en.md' instead of the file content.
"""
from __future__ import annotations

import sys

# Repo root supplied by caller as argv[1]; bterminal package importable
# from there.
REPO = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, REPO)

from bterminal.updater import _read_local_license, _fetch_remote_license  # noqa: E402

text = _read_local_license()
assert text, "local license empty"
assert text.lstrip().startswith("#"), (
    f"local license does not look like markdown: {text[:80]!r}"
)
assert text.strip() != "defaults/license/LICENSE.en.md", (
    "BUG #52 regression: license is path string, not content"
)
print("local-license-ok")

remote = _fetch_remote_license()
if remote:
    assert remote.lstrip().startswith("#"), (
        f"remote license not markdown: {remote[:80]!r}"
    )
    assert remote.strip() != "defaults/license/LICENSE.en.md", (
        "BUG #52 remote regression"
    )
    print("remote-license-ok")
else:
    print("remote-license-skipped")
