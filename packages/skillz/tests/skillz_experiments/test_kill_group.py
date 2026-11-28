"""The SIGKILL-the-group cleanup treats a refused `killpg` of an exited leader as a gone group (gh-568)."""
from __future__ import annotations

import contextlib
import errno
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from skillz_experiments.harness._discovery import discover
from skillz_experiments.proof._execute import score

SLEEPER = [sys.executable, "-c", "import time;time.sleep(30)"]


def _refuse_after_exit(pid: int, _signal: int) -> None:
    # Wait for the exit without a reap, then refuse as macOS does for the group of an exited leader.
    with contextlib.suppress(ChildProcessError):
        _ = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
    raise PermissionError(errno.EPERM, "Operation not permitted")


def _refuse(_pid: int, _signal: int) -> None:
    raise PermissionError(errno.EPERM, "Operation not permitted")


def _gone(_pid: int, _signal: int) -> None:
    raise ProcessLookupError(errno.ESRCH, "No such process")


needs_waitid = pytest.mark.skipif(not hasattr(os, "waitid"), reason="os.waitid exists on macOS only from Python 3.13")


@pytest.mark.ac("gh-568:AC-1")
@needs_waitid
def test_discovery_returns_its_verdict_when_the_kill_of_an_exited_child_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = "import sys;sys.stdin.readline();print('{\"id\": 0, \"error\": {}}', flush=True)"
    monkeypatch.setattr(os, "killpg", _refuse_after_exit)
    assert discover([sys.executable, "-c", script], tmp_path, {"PATH": os.defpath}, 30, "skillz") is False


@pytest.mark.ac("gh-568:AC-1")
@needs_waitid
def test_discovery_raises_its_own_error_when_the_kill_of_an_exited_child_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = "import sys;sys.stdin.readline()"
    monkeypatch.setattr(os, "killpg", _refuse_after_exit)
    with pytest.raises(RuntimeError, match="gave no result"):
        _ = discover([sys.executable, "-c", script], tmp_path, {"PATH": os.defpath}, 30, "skillz")


@pytest.mark.ac("gh-568:AC-1")
@needs_waitid
def test_proof_score_survives_a_refused_kill_of_an_exited_grader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(os, "killpg", _refuse_after_exit)
    assert score(tmp_path, (sys.executable, "-I", "-c", "print('{\"score\": 1}')"), os.defpath) == 1.0


def test_kill_group_sends_sigkill_to_the_group() -> None:
    from skillz_experiments._runtime import kill_group

    with subprocess.Popen(SLEEPER, start_new_session=True) as child:
        kill_group(child)
        assert child.wait(timeout=10) == -signal.SIGKILL


@pytest.mark.ac("gh-568:AC-1")
def test_kill_group_swallows_a_refused_kill_of_an_exited_child(monkeypatch: pytest.MonkeyPatch) -> None:
    from skillz_experiments._runtime import kill_group

    with subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True) as child:
        _ = child.wait(timeout=10)
        monkeypatch.setattr(os, "killpg", _refuse)
        kill_group(child)


@pytest.mark.ac("gh-568:AC-3")
def test_kill_group_raises_a_refused_kill_of_a_running_child(monkeypatch: pytest.MonkeyPatch) -> None:
    from skillz_experiments._runtime import kill_group

    with subprocess.Popen(SLEEPER, start_new_session=True) as child:
        try:
            with monkeypatch.context() as patch:
                patch.setattr(os, "killpg", _refuse)
                with pytest.raises(PermissionError):
                    kill_group(child)
            assert child.poll() is None
        finally:
            child.kill()


def test_kill_group_swallows_a_gone_group(monkeypatch: pytest.MonkeyPatch) -> None:
    from skillz_experiments._runtime import kill_group

    with subprocess.Popen(SLEEPER, start_new_session=True) as child:
        try:
            with monkeypatch.context() as patch:
                patch.setattr(os, "killpg", _gone)
                kill_group(child)
        finally:
            child.kill()
