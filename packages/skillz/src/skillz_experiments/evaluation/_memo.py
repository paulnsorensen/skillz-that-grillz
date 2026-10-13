"""The session memo of the local candidate contract check (ADR-013, O1 cascade first stage)."""
from __future__ import annotations

import threading
from typing import Protocol, final

from skillz_experiments._candidate import Candidate


class Checker(Protocol):
    """The members of a task transport that the contract check uses."""

    def check_candidate(self, candidate: Candidate) -> bool: ...

    @property
    def rejection(self) -> str | None: ...


@final
class CheckMemo:
    """Map each candidate identity to its `check_candidate` result and rejection reason.

    One session owns one memo. The memo is never written to disk. The run contract is frozen, so one identity always
    gets the same result in a run. The memo stores every rejection for the whole run, including a sandbox timeout.
    Thus a flaky helper fixture can reject a candidate for the run. Issue #287 tracks retryable timeouts. One lock
    covers the check and the read of `rejection`, because a transport keeps the reason of its last check in shared
    state. A cached result needs no lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, tuple[bool, str | None]] = {}

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
                self._results[identity] = found
            return found
