"""The session memo of the local candidate contract check (ADR-013, O1 cascade first stage)."""
from __future__ import annotations

import threading
from typing import Protocol, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._evaluation import transient


class Checker(Protocol):
    """The members of a task transport that the contract check uses."""

    def check_candidate(self, candidate: Candidate) -> bool: ...

    @property
    def rejection(self) -> str | None: ...


TIMEOUT_CHECKS = 2
"""The number of transient timeouts after which the memo keeps the timeout rejection of an identity."""


@final
class CheckMemo:
    """Map each candidate identity to its `check_candidate` result and rejection reason.

    One session owns one memo. The memo is never written to disk. The run contract is frozen, so one identity gets
    the same result in a run, except when a helper fixture times out in the sandbox. The Claude harness sandbox
    reports a timeout as exit code 124. The Codex harness raises `BudgetExhausted` instead. The memo does not keep
    the first timeout rejection of an identity, so a later evaluation checks again. After `TIMEOUT_CHECKS` timeouts,
    the memo keeps the rejection like any other. Thus a helper that always times out runs at most twice. Known
    limitation: a helper that itself exits 124 counts as a timeout. One lock covers the check and the read of
    `rejection`, because a transport keeps the reason of its last check in shared state. A cached result needs no
    lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, tuple[bool, str | None]] = {}
        self._timeouts: dict[str, int] = {}

    def check(self, task: Checker, candidate: Candidate) -> tuple[bool, str | None]:
        """Return whether `candidate` passes the contract check, and the rejection reason when it fails."""
        identity = candidate.identity
        found = self._results.get(identity)
        if found is not None:
            return found
        with self._lock:
            found = self._results.get(identity)
            if found is None:
                passed = task.check_candidate(candidate)
                found = (passed, None if passed else task.rejection)
                if transient(found[1]):
                    self._timeouts[identity] = self._timeouts.get(identity, 0) + 1
                if not transient(found[1]) or self._timeouts[identity] >= TIMEOUT_CHECKS:
                    self._results[identity] = found
            return found
