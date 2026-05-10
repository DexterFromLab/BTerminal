"""Pre-flight (#89 / #17) — print the EXACT argv BT would use to spawn
Aider, so the bash runner uses the same flags BT does.

The runner captures this output, parses it, and re-uses it. That way
if AiderProvider.build_argv changes (e.g. someone adds --auto-accept),
the smoke run automatically tracks the change instead of going stale.

Args:
  argv[1]: BTerminal repo path
  argv[2]: project_dir to embed in the spawn argv
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

REPO = sys.argv[1]
PROJECT_DIR = sys.argv[2]
sys.path.insert(0, REPO)

from bterminal.providers.aider import AiderProvider  # noqa: E402

# AiderProvider needs the per-provider config dict (capabilities + argv
# spec + binary spec), normally fed by ProviderRegistry. Load it from
# the bundled defaults.json so this helper stays isolated from
# ~/.config/bterminal/providers.json.
defaults_path = Path(REPO) / "bterminal" / "providers" / "defaults.json"
with open(defaults_path, encoding="utf-8") as fh:
    defaults = json.load(fh)
aider_config = defaults["providers"]["aider"]

prov = AiderProvider(aider_config)
binary = prov.find_binary()
if not binary:
    # Fall back to PATH lookup — needed when this helper runs from a
    # repo dir that doesn't shadow the real ~/.local/bin.
    binary = shutil.which("aider")

if not binary:
    print("aider-binary-missing", file=sys.stderr)
    sys.exit(2)

# Stub _binary_spec.binary so build_argv emits our resolved binary.
prov._binary_spec["binary"] = binary  # noqa: SLF001 — test-only

config = {
    "project_dir": PROJECT_DIR,
    "provider_options": {
        # All other defaults flow from capabilities.
    },
}
intro = ""  # not used by Aider — see build_argv docstring
argv = prov.build_argv(config, intro)
assert argv, "build_argv returned empty list — check find_binary"

# Print as a shell-quoted command line so the bash runner can simply
# eval $(python3 argv_parity.py ...) — no JSON parsing needed.
print(" ".join(shlex.quote(a) for a in argv))
