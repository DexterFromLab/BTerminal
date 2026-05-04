"""SidecarRunner lifecycle tests with real /bin/sleep subprocess.

Real subprocess > mock here: we want to verify that terminate→wait→kill
actually reaps the child and that subprocess.Popen wiring (cwd, env,
start_new_session) holds.
"""

import time

import pytest

from bterminal import SidecarManifest, SidecarRunner


def _sleeper(name: str = "t") -> SidecarManifest:
    return SidecarManifest(name=name, run_command="sleep 9999")


def test_start_stop_dummy():
    runner = SidecarRunner()
    assert runner.is_running("t") is False

    res = runner.start("t", _sleeper())
    assert res["already_running"] is False
    assert res["pid"] > 0
    pid = res["pid"]

    # Tiny grace so the child is past fork() before we probe
    time.sleep(0.1)
    assert runner.is_running("t") is True

    # Idempotent re-start returns the same PID
    res2 = runner.start("t", _sleeper())
    assert res2["already_running"] is True
    assert res2["pid"] == pid

    # Graceful stop
    stop = runner.stop("t")
    assert stop["was_running"] is True
    assert runner.is_running("t") is False

    # Idempotent stop
    stop2 = runner.stop("t")
    assert stop2["was_running"] is False


def test_stop_all_kills_every_child():
    runner = SidecarRunner()
    runner.start("a", _sleeper("a"))
    runner.start("b", _sleeper("b"))
    time.sleep(0.1)
    assert runner.is_running("a") and runner.is_running("b")

    runner.stop_all()
    assert not runner.is_running("a")
    assert not runner.is_running("b")


def test_start_with_empty_run_command_raises():
    runner = SidecarRunner()
    with pytest.raises(RuntimeError, match="empty run_command"):
        runner.start("x", SidecarManifest(name="x"))
