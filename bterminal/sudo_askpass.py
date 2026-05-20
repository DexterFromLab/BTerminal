"""SudoAskpassCache — shared sudo password helper for AI sessions.

When the user types their sudo password into the BTerminal sudo dialog, we
cache it as an executable askpass script at ``/tmp/bt-askpass-shared-XXXXXX``.
Any subprocess that needs root can be launched with ``sudo -A <cmd>`` and
``SUDO_ASKPASS=<this_script>`` in its environment — sudo execs the script
and reads the password from its stdout.

The script is mode 0700 so only the current uid can read it. ``clear()``
and ``__del__`` unlink it; ``ensure()`` always replaces any prior file so
re-entering a password never leaks old helpers.
"""

import os
import shlex
import subprocess
import tempfile


class SudoAskpassCache:
    def __init__(self):
        self._path = None

    def ensure(self, password):
        """Create an askpass helper for ``password`` and verify it with sudo.

        Returns True if the password is accepted (``sudo -A true`` → rc 0).
        On failure, the helper is unlinked and ``self._path`` resets to None.
        Always replaces any previously cached helper.
        """
        # Drop any existing helper first — no leaks on re-entry.
        self.clear()

        fd, path = tempfile.mkstemp(prefix="bt-askpass-shared-", dir="/tmp")
        try:
            script = "#!/bin/bash\necho {}\n".format(shlex.quote(password))
            os.write(fd, script.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(path, 0o700)

        # BUG#31g: component tests set BTERMINAL_TEST_FAKE_SUDO=1 so the
        # cache can be exercised end-to-end through REST without granting
        # the test runner real root. Empty password still fails so the
        # error-path branch stays covered.
        if os.environ.get("BTERMINAL_TEST_FAKE_SUDO") == "1":
            if not password:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                self._path = None
                return False
            self._path = path
            return True

        env = dict(os.environ)
        env["SUDO_ASKPASS"] = path
        try:
            # BUG#31j: drop any cached sudo timestamp first. Without this,
            # a recently-authenticated user could submit ANY password (even
            # one wrong character) and `sudo -A true` would still return 0
            # — it never even calls askpass when credentials are fresh.
            # The bogus password then lands in the cache and breaks the
            # AI session's first real `sudo -A <cmd>` once the timestamp
            # actually expires.
            subprocess.run(
                ["sudo", "-K"],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            result = subprocess.run(
                ["sudo", "-A", "true"],
                env=env,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ok = result.returncode == 0
        except Exception:
            ok = False

        if not ok:
            try:
                os.unlink(path)
            except OSError:
                pass
            self._path = None
            return False

        self._path = path
        return True

    def get_path(self):
        return self._path

    def is_set(self):
        return self._path is not None

    def clear(self):
        if self._path is None:
            return
        try:
            os.unlink(self._path)
        except OSError:
            pass
        self._path = None

    def __del__(self):
        try:
            self.clear()
        except Exception:
            pass
