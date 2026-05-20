"""Unit tests for SudoAskpassCache (BUG#31a foundation).

All tests mock subprocess.run so they never touch real sudo.
"""

import os
import stat
import tempfile
from unittest.mock import MagicMock, patch

from bterminal.sudo_askpass import SudoAskpassCache


def _rc(code):
    """Return value for subprocess.run mock — yields the given rc."""
    return MagicMock(returncode=code)


def test_get_path_none_initially():
    cache = SudoAskpassCache()
    assert cache.get_path() is None
    assert cache.is_set() is False


def test_ensure_creates_0700_tempfile():
    cache = SudoAskpassCache()
    with patch(
        "bterminal.sudo_askpass.subprocess.run", return_value=_rc(0)
    ):
        assert cache.ensure("secret") is True
    path = cache.get_path()
    assert path is not None
    assert os.path.isfile(path)
    assert os.path.basename(path).startswith("bt-askpass-shared-")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & 0o777 == 0o700, "expected 0700, got {}".format(oct(mode))
    cache.clear()


def test_ensure_returns_false_invalid_password():
    cache = SudoAskpassCache()
    captured_paths = []
    real_mkstemp = tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        captured_paths.append(path)
        return fd, path

    with patch("bterminal.sudo_askpass.tempfile.mkstemp", side_effect=spy_mkstemp):
        with patch(
            "bterminal.sudo_askpass.subprocess.run", return_value=_rc(1)
        ):
            assert cache.ensure("wrong-pw") is False

    assert cache.get_path() is None
    assert cache.is_set() is False
    assert len(captured_paths) == 1, "ensure should call mkstemp exactly once"
    assert not os.path.exists(captured_paths[0]), \
        "helper file must be unlinked on rc != 0"


def test_ensure_returns_true_valid():
    cache = SudoAskpassCache()
    with patch(
        "bterminal.sudo_askpass.subprocess.run", return_value=_rc(0)
    ):
        assert cache.ensure("good-pw") is True
    path = cache.get_path()
    assert path is not None
    assert os.path.isfile(path), "helper file must persist on rc == 0"
    cache.clear()


def test_ensure_twice_replaces_old_file():
    cache = SudoAskpassCache()
    with patch(
        "bterminal.sudo_askpass.subprocess.run", return_value=_rc(0)
    ):
        assert cache.ensure("first") is True
        first_path = cache.get_path()
        assert first_path and os.path.exists(first_path)

        assert cache.ensure("second") is True
        second_path = cache.get_path()

    assert first_path != second_path, "ensure must rotate the helper path"
    assert not os.path.exists(first_path), "old helper leaked after re-ensure"
    assert os.path.exists(second_path)
    cache.clear()


def test_ensure_calls_sudo_K_before_verify():
    """BUG#31j regression: ensure() must invalidate cached sudo
    credentials before running `sudo -A true`. Without that, a user
    whose sudo timestamp is still fresh from an earlier auth can submit
    ANY password (even one wrong character) and verification falsely
    passes — sudo never even invokes askpass when creds are cached.
    """
    cache = SudoAskpassCache()
    calls = []

    def record(args, **kwargs):
        calls.append(list(args))
        return _rc(0)

    with patch("bterminal.sudo_askpass.subprocess.run", side_effect=record):
        assert cache.ensure("pw") is True

    assert len(calls) >= 2, "expected sudo -K + sudo -A true"
    assert calls[0] == ["sudo", "-K"], (
        "first sudo invocation must drop cached credentials, "
        "got: {}".format(calls[0])
    )
    assert calls[1] == ["sudo", "-A", "true"], (
        "second invocation must verify via askpass, got: {}".format(calls[1])
    )
    cache.clear()


def test_clear_idempotent_unlink():
    cache = SudoAskpassCache()
    with patch(
        "bterminal.sudo_askpass.subprocess.run", return_value=_rc(0)
    ):
        assert cache.ensure("pw") is True
    path = cache.get_path()
    cache.clear()
    assert cache.get_path() is None
    assert cache.is_set() is False
    assert not os.path.exists(path)

    # Second clear() must not raise on already-None state.
    cache.clear()
    assert cache.is_set() is False

    # And it must not raise even if _path points to an already-deleted file.
    cache._path = path  # path was unlinked above
    cache.clear()
    assert cache.get_path() is None
