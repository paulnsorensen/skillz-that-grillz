"""The session memo of the local candidate contract check (ADR-013, O1 cascade first stage)."""
from __future__ import annotations

import threading
from typing import Protocol, final

from skillz_experiments._candidate import Candidate
from skillz_experiments._evaluation import CheckResult


class Checker(Protocol):
    """The member of a task transport that the contract check uses."""

    def check_candidate(self, candidate: Candidate) -> CheckResult: ...


@final
class CheckMemo:
    """Map each candidate identity to the `CheckResult` of its `check_candidate` call.

    One session owns one memo. The memo is never written to disk. The run contract is frozen, so one identity always
    gets the same result in a run. The memo stores every rejection for the whole run, including a sandbox timeout.
    Thus a flaky helper fixture can reject a candidate for the run. Issue #287 tracks retryable timeouts.
    Each identity has its own lock, so checks of different identities run at the same time. A short mutex guards only
    the lock map. A cached result needs no lock.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._results: dict[str, CheckResult] = {}

    def check(self, task: Checker, candidate: Candidate) -> CheckResult:
        """Return the contract check result of `candidate`. Check each identity once per memo."""
        identity = candidate.identity
        found = self._results.get(identity)
        if found is not None:
            return found
        with self._mutex:
            lock = self._locks.setdefault(identity, threading.Lock())
        with lock:
            found = self._results.get(identity)
            if found is None:
                found = task.check_candidate(candidate)
                self._results[identity] = found
            return found
