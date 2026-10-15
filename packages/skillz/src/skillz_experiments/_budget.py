from __future__ import annotations

import math
import threading
import time
from dataclasses import KW_ONLY, dataclass, field

from skillz_experiments._cases import CodedError

SECONDS_PER_CALL = 60
MAX_CONCURRENT_CALLS = 2
UNAPPROVED_CALLS = 40
UNAPPROVED_SECONDS = 2400
APPROVED_CALLS = 200
APPROVED_SECONDS = 7200
JOURNEY_CALLS = 1000
JOURNEY_SECONDS = 28800


class BudgetExhausted(CodedError):
    """The call budget or the deadline is exhausted. The agent starts a new run directory.

    A `ValueError` subclass through `CodedError`; it is a runtime stop, not a validation failure.
    """

    def __init__(self, message: str) -> None:
        super().__init__("budget-exhausted", message)


class BudgetUnapproved(CodedError):
    def __init__(self, message: str) -> None:
        super().__init__("budget-unapproved", message)


def ceilings(journey: bool) -> tuple[int, int]:
    """Return the approved call and second caps. A run with journey cases gets the journey ceiling."""
    return (JOURNEY_CALLS, JOURNEY_SECONDS) if journey else (APPROVED_CALLS, APPROVED_SECONDS)


@dataclass(frozen=True)
class Estimate:
    """The size of a run. `journey` is True when the run holds journey cases and so uses the journey ceiling."""

    calls: int
    seconds: int
    journey: bool = False


def estimate(*, preflight_calls: int = 0, train: int = 0, validation: int = 0, holdout: int = 0,
             calls_per_evaluation: int = 1, repeats: int = 1, search_calls: int = 0,
             reflection_calls: int = 0, retry_calls: int = 0, replay_calls: int = 0, journey: bool = False) -> Estimate:
    """Size a run before any model call.

    `train`, `validation`, and `holdout` are case counts per split.
    calls = preflight_calls + (train + validation + holdout) * calls_per_evaluation * repeats
    + search_calls + reflection_calls + retry_calls + replay_calls.
    seconds = ceil(calls * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS).
    Raise `ValueError` when a count is negative or `calls_per_evaluation` or `repeats` is below 1.
    """
    counts = (preflight_calls, train, validation, holdout, search_calls, reflection_calls, retry_calls, replay_calls)
    if min(counts) < 0 or calls_per_evaluation < 1 or repeats < 1:
        raise ValueError("estimate needs nonnegative counts and at least 1 call per evaluation and 1 repeat")
    cases = train + validation + holdout
    calls = (preflight_calls + cases * calls_per_evaluation * repeats + search_calls + reflection_calls + retry_calls
             + replay_calls)
    return Estimate(calls, _seconds(calls), journey)


def _seconds(calls: int) -> int:
    return math.ceil(calls * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS)


@dataclass
class Budget:
    """The call and time budget of one run.

    `reserve` holds calls for holdout claims. `reserve_seconds` holds the end of the deadline for the
    holdout gate: while it is above 0, `remaining` treats that time as spent. Set it with `reserve_time`.
    """

    maximum: int
    seconds: float
    reserve: int = 6
    calls: int = 0
    started: float = field(default_factory=time.monotonic)
    _: KW_ONLY
    approved: bool = False
    reserve_seconds: float = 0.0
    journey: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        max_calls, max_seconds = self._caps()
        if not 1 <= self.maximum <= max_calls or not 0 < self.seconds <= max_seconds:
            raise ValueError(f"budget exceeds {max_calls} calls or {max_seconds} seconds")
        if not 0 <= self.reserve <= self.maximum:
            raise ValueError("invalid holdout reservation")
        self._check_reserve_seconds(self.reserve_seconds)

    @classmethod
    def expired(cls, maximum: int, *, reserve: int, calls: int, approved: bool, journey: bool) -> Budget:
        """Return a budget whose deadline passed one second ago. Every `remaining` call raises."""
        return cls(maximum, 1.0, reserve=reserve, calls=calls, started=time.monotonic() - 1.0,
                   approved=approved, journey=journey)

    def _caps(self) -> tuple[int, int]:
        if not self.approved:
            return UNAPPROVED_CALLS, UNAPPROVED_SECONDS
        return ceilings(self.journey)

    def remaining(self) -> float:
        """Return the seconds left before the deadline, less any time that the holdout gate still holds."""
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExhausted("global deadline exhausted")
        reserved = self.reserve_seconds
        if remaining <= reserved:
            raise BudgetExhausted("search deadline exhausted; the holdout gate holds the remaining time")
        return remaining - reserved

    def reserve_time(self, seconds: float) -> None:
        """Hold `seconds` at the end of the deadline for the holdout gate. A value of 0 gives the time back."""
        self._check_reserve_seconds(seconds)
        self.reserve_seconds = seconds

    def _check_reserve_seconds(self, seconds: float) -> None:
        if not 0 <= seconds <= self._caps()[1]:
            raise ValueError("invalid holdout time reservation")

    def check(self, count: int, *, holdout: bool = False) -> None:
        _ = self.remaining()
        limit = self.maximum if holdout else self.maximum - self.reserve
        if self.calls + count > limit:
            raise BudgetExhausted("global invocation budget exhausted")

    def claim(self, *, holdout: bool = False) -> None:
        with self._lock:
            self.check(1, holdout=holdout)
            self.calls += 1


def gate_seconds(calls: int) -> int:
    """Return the seconds to hold for `calls` gate calls: ceil(calls * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS)."""
    return _seconds(max(calls, 0))


def seconds_cap_note(journey: bool) -> str:
    """Return the note that the seconds cap binds before the call cap on a journey run, or an empty string."""
    if not journey:
        return ""
    return f"; the seconds cap binds first at about {ceilings(True)[1] * MAX_CONCURRENT_CALLS // SECONDS_PER_CALL} calls"


def approve(sized: Estimate, approved_calls: int | None, *, reserve: int, reserve_time: int = 0) -> Budget:
    """Return the opt-in Budget for a sized run. Raise `BudgetUnapproved` without a matching approval.

    The ceiling is `sized.calls` calls and the approved seconds cap, because the
    estimate assumes ideal concurrency and the wall clock needs headroom.
    A journey estimate gets `JOURNEY_SECONDS` and the journey call cap; any other estimate gets `APPROVED_SECONDS`.
    `reserve_time` is the number of seconds at the end of the deadline that the holdout gate holds.
    Raise `ValueError` when the estimate has no calls.
    """
    if sized.calls < 1:
        raise ValueError("an approved run needs at least 1 call")
    max_calls, max_seconds = ceilings(sized.journey)
    if sized.calls > max_calls or sized.seconds > max_seconds:
        cap = f"{max_calls} calls and {max_seconds} seconds"
        cap += seconds_cap_note(sized.journey)
        raise BudgetUnapproved(f"estimate of {sized.calls} calls and {sized.seconds} seconds exceeds the cap of {cap}")
    if approved_calls is None or approved_calls < sized.calls:
        raise BudgetUnapproved(f"approve {sized.calls} calls (about {sized.seconds} seconds) before any model call")
    return Budget(sized.calls, max_seconds, min(reserve, sized.calls), approved=True,
                  reserve_seconds=min(reserve_time, sized.seconds), journey=sized.journey)
