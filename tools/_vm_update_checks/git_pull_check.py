"""Phase 3 (#86 / #14) — exercise _git_pull_with_autostash on a fake repo.

Caller has already set up:
  - a bare 'upstream' repo at argv[2]
  - a working clone at argv[3] checked out to VERSION=1.2.0 with
    upstream master pointing at a newer commit (VERSION=99.0.0)

This script imports updater helpers from argv[1] (BTerminal repo path),
runs `_git_repo_is_dirty` + `_git_pull_with_autostash` on the working
clone, and asserts VERSION post-pull == '99.0.0'.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = sys.argv[1]
LOCAL_CLONE = sys.argv[3]
sys.path.insert(0, REPO)

from bterminal.updater import (  # noqa: E402
    _git_pull_with_autostash,
    _git_repo_is_dirty,
)

assert not _git_repo_is_dirty(LOCAL_CLONE), (
    f"{LOCAL_CLONE} unexpectedly dirty pre-pull"
)
result = _git_pull_with_autostash(LOCAL_CLONE)
assert result.get("ok"), f"pull failed: {result!r}"

after = (Path(LOCAL_CLONE) / "VERSION").read_text().strip()
assert after == "99.0.0", f"VERSION did not advance: {after!r}"
print("pull-ok")
