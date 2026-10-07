from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path

from skillz_experiments._cases import CodedError

SECONDS_PER_CALL = 60
MAX_CONCURRENT_CALLS = 2
UNAPPROVED_CALLS = 40
UNAPPROVED_SECONDS = 2400
APPROVED_CALLS = 200
APPROVED_SECONDS = 7200

_CHILDREN = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)


class BudgetExhausted(CodedError):
    """The call budget or the deadline ran out. The agent starts a new run directory."""

    def __init__(self, message: str) -> None:
        super().__init__("budget-exhausted", message)


class BudgetUnapproved(CodedError):
    def __init__(self, message: str) -> None:
        super().__init__("budget-unapproved", message)


@dataclass(frozen=True)
class Estimate:
    calls: int
    seconds: int


def estimate(*, preflight_calls: int = 0, train: int = 0, validation: int = 0, holdout: int = 0,
             calls_per_evaluation: int = 1, repeats: int = 1, search_calls: int = 0,
             reflection_calls: int = 0, retry_calls: int = 0) -> Estimate:
    """Size a run before any model call.

    `train`, `validation`, and `holdout` are case counts per split.
    calls = preflight_calls + (train + validation + holdout) * calls_per_evaluation * repeats
    + search_calls + reflection_calls + retry_calls.
    seconds = ceil(calls * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS).
    """
    cases = train + validation + holdout
    calls = preflight_calls + cases * calls_per_evaluation * repeats + search_calls + reflection_calls + retry_calls
    return Estimate(calls, math.ceil(calls * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS))


@dataclass
class Budget:
    maximum: int
    seconds: float
    reserve: int = 6
    calls: int = 0
    started: float = field(default_factory=time.monotonic)
    _: KW_ONLY
    approved: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        max_calls = APPROVED_CALLS if self.approved else UNAPPROVED_CALLS
        max_seconds = APPROVED_SECONDS if self.approved else UNAPPROVED_SECONDS
        if not 1 <= self.maximum <= max_calls or not 0 < self.seconds <= max_seconds:
            raise ValueError(f"budget exceeds {max_calls} calls or {max_seconds} seconds")
        if not 0 <= self.reserve <= self.maximum:
            raise ValueError("invalid holdout reservation")

    def remaining(self) -> float:
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExhausted("global deadline exhausted")
        return remaining

    def check(self, count: int, *, holdout: bool = False) -> None:
        _ = self.remaining()
        limit = self.maximum if holdout else self.maximum - self.reserve
        if self.calls + count > limit:
            raise BudgetExhausted("global invocation budget exhausted")

    def claim(self, *, holdout: bool = False) -> None:
        with self._lock:
            self.check(1, holdout=holdout)
            self.calls += 1


def approve(sized: Estimate, approved_calls: int | None, *, reserve: int = 6) -> Budget:
    """Return the opt-in Budget for a sized run. Raise `BudgetUnapproved` without a matching approval.

    The ceiling is `sized.calls` calls and `APPROVED_SECONDS` seconds, because the
    estimate assumes ideal concurrency and the wall clock needs headroom.
    """
    if sized.calls > APPROVED_CALLS or sized.seconds > APPROVED_SECONDS:
        cap = f"{APPROVED_CALLS} calls and {APPROVED_SECONDS} seconds"
        raise BudgetUnapproved(f"estimate of {sized.calls} calls and {sized.seconds} seconds exceeds the cap of {cap}")
    if approved_calls is None or approved_calls < sized.calls:
        raise BudgetUnapproved(f"approve {sized.calls} calls (about {sized.seconds} seconds) before any model call")
    return Budget(max(sized.calls, 1), APPROVED_SECONDS, min(reserve, max(sized.calls, 1)), approved=True)


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
