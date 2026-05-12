"""Regression: curl hook wrapper must run under bash, not dash.

install.sh writes a tiny curl wrapper to a temp dir during the Ollama
phase, then PATH-injects it so ollama.com/install.sh sees a curl that
strips --progress-bar / -s (DOWNLOAD POLICY: every download must show
% / speed / ETA).

Bug 2026-05-12 (apneus2 / Linux Mint 22.3, /bin/sh -> dash):
the wrapper was generated with `#!/bin/sh` and used `printf %q` —
a bashism. Dash aborted with `printf: %q: invalid directive`, the
wrapper produced no output, ollama installer's `curl|sh` pipe got
nothing, and install.sh died with exit 143 (SIGPIPE/kill from `set -e`
on the failed pipeline).

Pin both halves:
  (a) the generated wrapper must use `#!/bin/bash` (any future
      contributor switching to `#!/bin/sh` will be caught here);
  (b) the body must parse under `bash -n` — guards against further
      syntax slips inside the heredoc.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"


def _extract_hook_body() -> str:
    """Pull the wrapper body from install.sh's heredoc.

    Returns the literal text that would land at $CURL_HOOK_DIR/curl
    after install.sh runs the `cat > ... <<HOOK_EOF` block. The two
    \\$ escapes that bash strips inside an unquoted heredoc are
    converted back to $ so the result is what the wrapper actually
    sees on disk.
    """
    text = INSTALL_SH.read_text()
    m = re.search(
        r'cat\s*>\s*"\$CURL_HOOK_DIR/curl"\s*<<HOOK_EOF\n(.*?)\nHOOK_EOF',
        text,
        re.DOTALL,
    )
    assert m, "curl hook heredoc not found in install.sh"
    body = m.group(1)
    return body.replace(r"\$", "$")


def test_curl_hook_uses_bash_shebang():
    body = _extract_hook_body()
    first_line = body.splitlines()[0]
    assert first_line == "#!/bin/bash", (
        f"curl wrapper must be #!/bin/bash (dash lacks printf %q and bash arrays). "
        f"Got: {first_line!r}"
    )


def test_curl_hook_body_parses_under_bash():
    body = _extract_hook_body()
    bash = shutil.which("bash")
    assert bash, "bash not installed — required to syntax-check the hook"
    proc = subprocess.run(
        [bash, "-n", "/dev/stdin"],
        input=body,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, (
        f"curl wrapper has bash syntax error:\n{proc.stderr}\n---body---\n{body}"
    )


def test_curl_hook_strips_progress_and_silent_flags():
    body = _extract_hook_body()
    assert "--progress-bar" in body, "wrapper must still strip --progress-bar"
    assert "--silent" in body, "wrapper must still strip --silent"
    assert "-s)" in body, "wrapper must still strip -s short form"


def test_curl_hook_does_not_use_printf_q_directive():
    """`printf %q` is a bashism. Even with #!/bin/bash it's brittle —
    array-based forwarding is cleaner. Catch any future regression."""
    body = _extract_hook_body()
    code_lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "printf %q" not in code and 'printf "%q"' not in code, (
        "curl wrapper uses `printf %q` — switch to array forwarding instead"
    )
