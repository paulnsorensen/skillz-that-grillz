from __future__ import annotations

import math
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import KW_ONLY, dataclass, field

from skillz_experiments._cases import CodedError

SECONDS_PER_CALL = 60
"""The slow-call rate: the per-call timeout base and the gate time reserve rate (ADR-005)."""
ESTIMATE_SECONDS_PER_CALL = 30
"""The plan rate: the seconds that the estimate charges for each call (gh-571).

In 22 live runs the effective time per call was 5.5 to 21.7 seconds; 30 seconds keeps a margin over the slowest run.
"""
CALL_TIMEOUT_FACTOR = 4
# Every model call gets 1 more second of timeout per PROMPT_CHARS_PER_SECOND prompt characters.
# The rate fits the slowest case, a reflection call: its answer returns complete components, so it is about as long
# as its prompt. At about 4 characters per token and 50 output tokens per second, a model writes about 200 characters per second.
PROMPT_CHARS_PER_SECOND = 200
FAILED = "failed"
SEARCH_ROLES = ("task", "reflection", "judge", FAILED)
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


class CallTimeout(RuntimeError):
    """One model call ran past its per-call timeout while the search deadline was still open.

    It is not a `CodedError`: it fails one call, not the run.
    """


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
    seconds = ceil(calls * ESTIMATE_SECONDS_PER_CALL / MAX_CONCURRENT_CALLS).
    Raise `ValueError` when a count is negative or `calls_per_evaluation` or `repeats` is below 1.
    """
    counts = (preflight_calls, train, validation, holdout, search_calls, reflection_calls, retry_calls, replay_calls)
    if min(counts) < 0 or calls_per_evaluation < 1 or repeats < 1:
        raise ValueError("estimate needs nonnegative counts and at least 1 call per evaluation and 1 repeat")
    cases = train + validation + holdout
    calls = (preflight_calls + cases * calls_per_evaluation * repeats + search_calls + reflection_calls + retry_calls
             + replay_calls)
    return Estimate(calls, _seconds(calls), journey)


def _seconds(calls: int, per_call: int = ESTIMATE_SECONDS_PER_CALL) -> int:
    return math.ceil(calls * per_call / MAX_CONCURRENT_CALLS)


@dataclass(frozen=True)
class Spent:
    """What a run has spent at one moment."""

    calls: int
    retries: int
    calls_by_role: dict[str, int]
    seconds_by_role: dict[str, float]


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
    retries: int = 0
    call_seconds: float = SECONDS_PER_CALL
    calls_by_role: dict[str, int] = field(default_factory=dict[str, int])
    seconds_by_role: dict[str, float] = field(default_factory=dict[str, float])
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        max_calls, max_seconds = self._caps()
        if not 1 <= self.maximum <= max_calls or not 0 < self.seconds <= max_seconds:
            raise ValueError(f"budget exceeds {max_calls} calls or {max_seconds} seconds")
        if not 0 <= self.reserve <= self.maximum:
            raise ValueError("invalid holdout reservation")
        self._check_reserve_seconds(self.reserve_seconds)

    @classmethod
    def expired(cls, maximum: int, *, reserve: int, calls: int, approved: bool, journey: bool, calls_by_role: dict[str, int],
                seconds_by_role: dict[str, float], retries: int, call_seconds: float) -> Budget:
        """Return a budget whose deadline has passed and that keeps the spent calls, retries, and role maps.

        A resumed run in the gated or replayed phase uses it, so every model call stops at its deadline check
        and the recorded counts stay whole: `calls` equals the sum of `calls_by_role`.
        """
        return cls(maximum, 1.0, reserve, calls, time.monotonic() - 1.0, approved=approved, journey=journey,
                   retries=retries, call_seconds=call_seconds, calls_by_role=calls_by_role, seconds_by_role=seconds_by_role)

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

    def call_timeout(self, prompt_chars: int = 0) -> float:
        """Return the seconds that one model call may run, capped by `remaining`.

        The limit is `CALL_TIMEOUT_FACTOR` times the per-call estimate, plus 1 second per `PROMPT_CHARS_PER_SECOND`
        characters of the prompt. A large prompt, such as a reflection prompt that holds every editable file, gets more time.
        """
        scaled = CALL_TIMEOUT_FACTOR * self.call_seconds + prompt_chars / PROMPT_CHARS_PER_SECOND
        return min(scaled, self.remaining())

    def out_of_time(self) -> bool:
        """Return whether the search deadline has passed."""
        try:
            _ = self.remaining()
        except BudgetExhausted:
            return True
        return False

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

    def claim(self, *, holdout: bool = False, role: str = "task") -> None:
        """Claim one call for `role`. The per-role counts always sum to `calls`."""
        with self._lock:
            self.check(1, holdout=holdout)
            self.calls += 1
            self.calls_by_role[role] = self.calls_by_role.get(role, 0) + 1

    @contextmanager
    def timed(self, role: str) -> Generator[None]:
        """Add the wall seconds of the body to `role`, or to `failed` when the body raises."""
        started = time.monotonic()
        key = role
        try:
            yield
        except BaseException:
            key = FAILED
            raise
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                self.seconds_by_role[key] = self.seconds_by_role.get(key, 0.0) + elapsed

    def snapshot(self) -> Spent:
        """Return the calls, retries, and both role maps as of one moment, read under the budget lock."""
        with self._lock:
            return Spent(self.calls, self.retries, dict(self.calls_by_role), dict(self.seconds_by_role))

    def retry(self) -> None:
        """Count a rate-limit retry. A retry does not spend the approved call budget."""
        with self._lock:
            self.retries += 1


def gate_seconds(calls: int) -> int:
    """Return the seconds to hold for `calls` gate calls: ceil(calls * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS).

    The reserve uses the slow-call rate, not the plan rate, so slower gate calls can still reach the deadline (ADR-005).
    """
    return _seconds(max(calls, 0), SECONDS_PER_CALL)


def approve(sized: Estimate, approved_calls: int | None, *, reserve: int, reserve_time: int = 0) -> Budget:
    """Return the opt-in Budget for a sized run. Raise `BudgetUnapproved` without a matching approval.

    The ceiling is `sized.calls` calls and the approved seconds cap, because the
    estimate assumes ideal concurrency and the wall clock needs headroom.
    A journey estimate gets `JOURNEY_SECONDS` and the journey call cap; any other estimate gets `APPROVED_SECONDS`.
    `reserve_time` is the number of seconds at the end of the deadline that the holdout gate holds, at most the
    seconds cap. The cap, not `sized.seconds`, bounds it, because the reserve charges the slow-call rate (ADR-005).
    Raise `ValueError` when the estimate has no calls.
    """
    if sized.calls < 1:
        raise ValueError("an approved run needs at least 1 call")
    max_calls, max_seconds = ceilings(sized.journey)
    if sized.calls > max_calls or sized.seconds > max_seconds:
        cap = f"{max_calls} calls and {max_seconds} seconds"
        raise BudgetUnapproved(f"estimate of {sized.calls} calls and {sized.seconds} seconds exceeds the cap of {cap}")
    if approved_calls is None or approved_calls < sized.calls:
        raise BudgetUnapproved(f"approve {sized.calls} calls (about {sized.seconds} seconds) before any model call")
    return Budget(sized.calls, max_seconds, min(reserve, sized.calls), approved=True,
                  reserve_seconds=min(reserve_time, max_seconds), journey=sized.journey)
