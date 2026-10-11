from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from typing import cast, final
from pathlib import Path

from skillz_experiments._budget import MAX_CONCURRENT_CALLS, BudgetExhausted

_CHILDREN = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)

def process(command: list[str], *, cwd: Path, timeout: float,
            environment: dict[str, str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a child under a call slot. Time spent waiting for the slot counts against `timeout`."""
    waiting = time.monotonic()
    if not _CHILDREN.acquire(timeout=max(timeout, 0)):
        raise BudgetExhausted("deadline exhausted waiting for a call slot")
    try:
        timeout -= time.monotonic() - waiting
        if timeout <= 0:
            raise BudgetExhausted("deadline exhausted waiting for a call slot")
        with subprocess.Popen(command, cwd=cwd, env=environment, text=True, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=True) as child:
            try:
                stdout, stderr = child.communicate(input_text, timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                _ = child.communicate()
                raise BudgetExhausted("process group terminated at deadline") from None
            except BaseException:
                os.killpg(child.pid, signal.SIGKILL)
                _ = child.communicate()
                raise
            return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
    finally:
        _ = _CHILDREN.release()


@final
class Live:
    """A child process that stays up across turns. Reader threads feed its output lines to a queue.

    A turn holds a call slot only while the child works, so a simulator call between two turns can take a slot.
    """

    def __init__(self, child: subprocess.Popen[str]) -> None:
        self.child: subprocess.Popen[str] = child
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.errors: list[str] = []
        self._threads: list[threading.Thread] = [threading.Thread(target=self._read, daemon=True),
                                                 threading.Thread(target=self._drain, daemon=True)]
        for thread in self._threads:
            thread.start()

    def _read(self) -> None:
        assert self.child.stdout is not None
        for line in cast(Iterable[str], self.child.stdout):
            self.lines.put(line)
        self.lines.put(None)

    def _drain(self) -> None:
        assert self.child.stderr is not None
        for line in cast(Iterable[str], self.child.stderr):
            if sum(map(len, self.errors)) < 8192:
                self.errors.append(line)

    @property
    def stderr(self) -> str:
        return "".join(self.errors)

    def kill(self) -> None:
        try:
            os.killpg(self.child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def turn(self, message: str, timeout: float, ends: Callable[[str], bool]) -> tuple[list[str], bool]:
        """Send one line, then collect output lines until `ends` accepts one.

        Return the lines and whether one ended the turn. The child exiting first ends the turn without a match.
        """
        waiting = time.monotonic()
        if not _CHILDREN.acquire(timeout=max(timeout, 0)):
            raise BudgetExhausted("deadline exhausted waiting for a call slot")
        try:
            deadline = waiting + timeout
            try:
                assert self.child.stdin is not None
                _ = self.child.stdin.write(message + "\n")
                self.child.stdin.flush()
            except OSError:
                pass
            collected: list[str] = []
            while True:
                left = deadline - time.monotonic()
                try:
                    if left <= 0:
                        raise queue.Empty
                    line = self.lines.get(timeout=left)
                except queue.Empty:
                    self.kill()
                    raise BudgetExhausted("process group terminated at deadline") from None
                if line is None:
                    self.lines.put(None)
                    return collected, False
                collected.append(line)
                if ends(line):
                    return collected, True
        finally:
            _ = _CHILDREN.release()

    def close(self) -> int:
        """End the child and its process group. Return the exit code."""
        try:
            assert self.child.stdin is not None
            self.child.stdin.close()
        except OSError:
            pass
        try:
            _ = self.child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self.kill()
        _ = self.child.wait()
        for thread in self._threads:
            thread.join(timeout=5)
        return cast(int, self.child.returncode)


@contextmanager
def live(command: list[str], *, cwd: Path, environment: dict[str, str]) -> Generator[Live]:
    """Start a child that reads lines on stdin and keep it up until the block ends."""
    child = subprocess.Popen(command, cwd=cwd, env=environment, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    session = Live(child)
    try:
        yield session
    finally:
        _ = session.close()
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                stream.close()
