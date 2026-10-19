"""The session memo of the local candidate contract check (ADR-013, O1 cascade first stage)."""
from __future__ import annotations

import threading
from typing import Protocol, final

from skillz_experiments._candidate import Candidate
from skillz_experiments.evaluation._scoring import CheckResult


class Checker(Protocol):
    """The member of a task transport that the contract check uses."""

    def check_candidate(self, candidate: Candidate) -> CheckResult: ...


@final
class CheckMemo:
    """Map each candidate identity to the final `CheckResult` of its `check_candidate` calls.

    One session owns one memo. The memo is never written to disk. The run contract is frozen, so one identity always
    gets the same result in a run. A check that times out in the sandbox runs once more at once, in the same `check`
    call. The second result is final for the run, so a second timeout proves a real hang. No caller sees a first
    timeout. A resume starts a new memo, so the seed check and the holdout and replay arms check again. A recorded
    search outcome stays final for the run.
    Each identity has its own lock, so checks of different identities run at the same time. A short mutex guards only
    the lock map. A cached result needs no lock.
    """

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._results: dict[str, CheckResult] = {}

    def check(self, task: Checker, candidate: Candidate) -> CheckResult:
        """Return the final contract check result of `candidate`. Check each identity at most twice per memo."""
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
                if found.kind == "timeout":
                    found = task.check_candidate(candidate)
                self._results[identity] = found
            return found
