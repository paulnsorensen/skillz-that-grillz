"""The fault latch of a run: the one coded error from a model call that ends the work.
"""
from __future__ import annotations

import threading
from typing import final

from skillz_experiments._budget import BudgetExhausted
from skillz_experiments._cases import CodedError
from skillz_experiments.harness import stop_kind


@final
class FaultLatch:
    """Hold one CodedError. Each method takes the lock of the session that owns the latch."""

    def __init__(self, lock: threading.RLock) -> None:
        self._lock = lock
        self._fault: CodedError | None = None

    @property
    def latched(self) -> bool:
        with self._lock:
            return self._fault is not None

    def latch(self, error: CodedError) -> None:
        """Keep one CodedError from a model call. A terminal code replaces a held fault with another code. A coded fault replaces a held budget stop. Otherwise the first fault stays."""
        with self._lock:
            held = self._fault
            if held is None:
                replace = True
            elif stop_kind(error.code) == "terminal":
                replace = stop_kind(held.code) != "terminal"
            else:
                replace = isinstance(held, BudgetExhausted) and not isinstance(error, BudgetExhausted)
            if replace:
                self._fault = error

    def raise_held(self) -> None:
        """Re-raise the first CodedError from a model call. A budget stop ends the search; any other code stops the run. GEPA turns each proposer exception into a skipped proposal."""
        with self._lock:
            fault = self._fault
        if fault is not None:
            raise fault

    def take_budget_stop(self) -> None:
        """Clear a latched budget stop so the gate can run. Re-raise any other latched fault."""
        with self._lock:
            fault = self._fault
            if fault is not None and not isinstance(fault, BudgetExhausted):
                raise fault
            self._fault = None

    def authoritative(self, error: Exception) -> Exception:
        """Return the fault that ends the run: a latched fault other than a budget stop, else `error`."""
        with self._lock:
            fault = self._fault
        if fault is not None and not isinstance(fault, BudgetExhausted):
            return fault
        return error
