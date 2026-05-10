"""Phase 5 (#89 / #17) — verify AiderProvider.parse_session_stats reads
the chat history written by a real aider invocation.

Args:
  argv[1]: BTerminal repo path (so `bterminal.providers.aider` imports)
  argv[2]: project_dir whose .aider.chat.history.md is the log
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = sys.argv[1]
PROJECT_DIR = sys.argv[2]
sys.path.insert(0, REPO)

from bterminal.providers.aider import AiderProvider  # noqa: E402

defaults_path = Path(REPO) / "bterminal" / "providers" / "defaults.json"
with open(defaults_path, encoding="utf-8") as fh:
    defaults = json.load(fh)
aider_config = defaults["providers"]["aider"]

prov = AiderProvider(aider_config)
log = prov.session_log_glob(PROJECT_DIR)
assert log, f"session_log_glob returned None for {PROJECT_DIR!r}"
assert os.path.isfile(log), f"chat history not at expected path: {log}"
print(f"chat-log-path={log}")

stats = prov.parse_session_stats(log)

# Aider always writes at least one user turn marker '#### ' for the
# prompt we sent. That's the minimal stats signal — proves the parser
# actually read the file.
assert stats.response_count >= 1, (
    f"expected response_count >= 1, got {stats.response_count}"
)
print(f"response-count={stats.response_count}")

# Model attribution: parse_session_stats either reads `--model X` from
# the log (when aider preserves invocation banner) OR falls back to
# capabilities.default_model. Either way it must NOT be None.
assert stats.model, f"stats.model unset — banner detection regressed"
print(f"model={stats.model}")

print("stats-check-ok")
