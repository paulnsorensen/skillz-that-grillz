"""The provider seam of a run, and the narrow view of a session that its phase modules read.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, Split
from skillz_experiments._contract import Contract
from skillz_experiments.evaluation import CheckMemo, CheckResult
from skillz_experiments.gate import Statistics
from skillz_experiments.run._fault import FaultLatch

HarnessName = str


class Provider(Protocol):
    def preflight(self) -> dict[str, object]: ...
    def close(self) -> None: ...
    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: CheckMemo) -> dict[str, object]: ...
    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]: ...
    def check_name(self, candidate: Candidate) -> None: ...
    def check_candidate(self, candidate: Candidate) -> CheckResult: ...


Factory = Callable[[str, Budget, Callable[[], None]], Provider]


class RunState(Protocol):
    """What a phase module reads and writes of its session. A phase runs under the session lock and takes none of its own."""

    out: Path
    lock: threading.RLock
    record: dict[str, object]
    outcomes: list[dict[str, object]]
    contract: Contract
    seed: Candidate
    statistics: Statistics
    budget: Budget
    provider: Provider
    faults: FaultLatch

    def checkpoint(self) -> None: ...
    def cases_for(self, split: Split) -> list[Case]: ...
    def evaluate_case(self, candidate: Candidate, case: Case, arm: str, *, holdout: bool = False,
                      repeat: int | None = None) -> dict[str, object]: ...
