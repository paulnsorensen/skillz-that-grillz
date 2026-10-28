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

from skillz_experiments._budget import MAX_CONCURRENT_CALLS, Budget, BudgetExhausted, CallTimeout

TERM_GRACE_SECONDS = 10.0
# The sandbox time limit of one Codex or command-harness sandbox run, and the default of `ClaudeOptions.sandbox_seconds`.
SANDBOX_SECONDS = 20


class SandboxTimeout(Exception):
    """A sandbox run reached the sandbox time limit of its harness while the run budget still had that time.

    It is not a `BudgetExhausted`, so no budget handler catches it. A run budget that ends a sandbox run still raises
    `BudgetExhausted`.
    """


@final
class CallSlots:
    """A counting semaphore whose permit limit can drop. `halve` never goes below 1."""

    def __init__(self, limit: int) -> None:
        self.maximum: int = limit
        self.limit: int = limit
        self._busy: int = 0
        self._changed: threading.Condition = threading.Condition()

    def acquire(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._changed:
            while self._busy >= self.limit:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                _ = self._changed.wait(left)
            self._busy += 1
            return True

    def release(self) -> None:
        with self._changed:
            if self._busy <= 0:
                raise ValueError("released a call slot that no call holds")
            self._busy -= 1
            self._changed.notify()

    def halve(self) -> int:
        """Halve the permit limit, to a minimum of 1, and return it. Running calls finish first."""
        with self._changed:
            self.limit = max(1, self.limit // 2)
            return self.limit

    def reset(self) -> None:
        """Restore the starting limit, for a new run."""
        with self._changed:
            self.limit = self.maximum
            self._changed.notify_all()


_CHILDREN = CallSlots(MAX_CONCURRENT_CALLS)


def halve_call_slots() -> int:
    """Halve the concurrent model calls for the rest of the run. Return the new limit."""
    return _CHILDREN.halve()


def reset_call_slots() -> None:
    _CHILDREN.reset()


def _end(child: subprocess.Popen[str]) -> None:
    """Send SIGTERM to the child's group, wait `TERM_GRACE_SECONDS`, then SIGKILL whatever remains.

    After the SIGKILL, wait for the child itself and do not read its pipes to the end. A descendant that left the
    group with `setsid()` can keep a pipe open, and reading to the end would wait for that descendant.
    """
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        _ = child.communicate(timeout=TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        _ = child.wait()


@contextmanager
def _slot(wait: float) -> Generator[None]:
    """Hold a call slot for the block. Raise `BudgetExhausted` when none frees within `wait` seconds."""
    if not _CHILDREN.acquire(timeout=max(wait, 0)):
        raise BudgetExhausted("deadline exhausted waiting for a call slot")
    try:
        yield
    finally:
        _CHILDREN.release()


def _spawn(command: list[str], cwd: Path, timeout: float, environment: dict[str, str],
           input_text: str | None) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(command, cwd=cwd, env=environment, text=True, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as child:
        try:
            stdout, stderr = child.communicate(input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            _end(child)
            raise BudgetExhausted("process group terminated at deadline") from None
        except BaseException:
            _end(child)
            raise
        return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)


def process(command: list[str], *, cwd: Path, timeout: float, environment: dict[str, str],
            input_text: str | None = None, slotted: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a child under a call slot. Time spent waiting for the slot counts against `timeout`.

    With `slotted`, the caller already holds the slot, and `timeout` limits the child alone.
    """
    if slotted:
        return _spawn(command, cwd, timeout, environment, input_text)
    waiting = time.monotonic()
    with _slot(timeout):
        timeout -= time.monotonic() - waiting
        if timeout <= 0:
            raise BudgetExhausted("deadline exhausted waiting for a call slot")
        return _spawn(command, cwd, timeout, environment, input_text)


def sandboxed(command: list[str], *, cwd: Path, limit: float, budget: Budget, environment: dict[str, str],
              input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one sandbox child under a call slot. The child gets `limit` seconds, or less when the run budget has less.

    The slot wait counts against the run budget, not against `limit`. A child that runs for `limit` seconds raises
    `SandboxTimeout`. A child that the run budget stops raises `BudgetExhausted`. Both outcomes kill the child
    process group.
    """
    with _slot(budget.remaining()):
        timeout = min(limit, budget.remaining())
        try:
            return _spawn(command, cwd, timeout, environment, input_text)
        except BudgetExhausted:
            if timeout < limit:
                raise
            raise SandboxTimeout(f"the sandbox run reached its {limit:g} second limit") from None


def model_call(budget: Budget, command: list[str], *, cwd: Path, environment: dict[str, str],
               input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one model call under its per-call timeout: `budget.call_timeout()` for the size of `input_text`.

    The remaining search time caps the timeout.

    The per-call clock starts after the call holds a slot. A slot wait that reaches the search deadline
    raises `BudgetExhausted`, never `CallTimeout`.
    A call that runs out its own timeout, while the search deadline is still open, raises `CallTimeout`.
    A call that the deadline stops raises `BudgetExhausted`.
    """
    with _slot(budget.remaining()):
        limit = budget.call_timeout(len(input_text or ""))
        try:
            return process(command, cwd=cwd, timeout=limit, environment=environment, input_text=input_text, slotted=True)
        except BudgetExhausted:
            if budget.out_of_time():
                raise
            raise CallTimeout(f"the model call exceeded its per-call timeout of {limit:g} seconds") from None


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
        except PermissionError:
            if self.child.poll() is None:
                raise

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
            _CHILDREN.release()

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
