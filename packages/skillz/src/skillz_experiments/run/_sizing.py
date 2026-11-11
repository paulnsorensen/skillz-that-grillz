"""Size a run against the approved call number, and show the repeats table when it does not fit (ADR-005, gh-588).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from skillz_experiments._budget import APPROVED_CALLS, JOURNEY_CALLS
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import Contract
from skillz_experiments.harness import ClaudeOptions
from skillz_experiments.run._edit_plan import ReflectionNotes
from skillz_experiments.run._plan import Plan, plan_estimate, size_plan
from skillz_experiments.run._stop import Stop

TABLE_REPEATS = (1, 2, 3)


def raised_cap(approve_budget: int | None, cases: Sequence[Case], *, flag: str = "--approve-budget") -> int:
    """Return the call cap that a task run sizes against, or 0 for the standard cap.

    `--approve-budget N` with `APPROVED_CALLS` < N <= `JOURNEY_CALLS` raises the cap to N. A larger N on a task run
    raises `budget-cap-exceeded`. A run with journey cases keeps the journey ceiling and ignores N here.
    """
    if approve_budget is None or approve_budget <= APPROVED_CALLS or any(case.journey is not None for case in cases):
        return 0
    if approve_budget > JOURNEY_CALLS:
        raise CodedError("budget-cap-exceeded", f"{flag} {approve_budget} is above the ceiling of {JOURNEY_CALLS} calls; "
                         + f"{'approve' if flag == '--approve-budget' else 'use'} at most {JOURNEY_CALLS}, "
                         + "or lower --repeats or the holdout size")
    return approve_budget


@dataclass(frozen=True)
class Sizer:
    """Call `size_plan` against one cap, and turn a failed plan into a `budget-unapproved` stop that shows what would fit.

    `shown` holds the fields that every `budget-unapproved` stop carries, such as `statistics`.
    """

    contract: Contract
    cases: Sequence[Case]
    options: ClaudeOptions
    cap: int
    repeats_given: bool
    holdout_cases: int
    notes: ReflectionNotes
    shown: Mapping[str, object]

    def estimate(self, plan: Plan, repeats: int) -> dict[str, object]:
        """Return the shown estimate of `plan`: the `plan_estimate` that `verify_budget` checks, and the reflection block."""
        return plan_estimate(plan, repeats, self.holdout_cases) | {"reflection": self.notes.time(plan.reflection_calls, plan.sized)}

    def plan(self, repeats: int) -> Plan:
        """Return the plan for `repeats`. A plan that does not fit stops with the smallest approval and the repeats table."""
        try:
            return size_plan(self.contract, self.cases, repeats, self.options, self.cap)
        except Stop as error:
            raise self.stop(str(error), {}, repeats, fitting=True) from None

    def stop(self, message: str, data: dict[str, object], repeats: int, *, fitting: bool = False) -> Stop:
        """Return the `budget-unapproved` stop. It adds the repeats table unless the author gave `--repeats`.

        With `fitting`, it also names the smallest approved number that fits, when one up to `JOURNEY_CALLS` does, and
        shows the `estimate` of the plan at that number, so the user sees what the approval covers.
        """
        text, extra = message, dict[str, object]()
        smallest = self._smallest(repeats) if fitting else None
        if smallest is not None:
            text += f"; --approve-budget {smallest} is the smallest approved number that fits"
            shown = self.estimate(size_plan(self.contract, self.cases, repeats, self.options, smallest), repeats)
            extra |= {"smallest_approval": smallest, "estimate": shown, "approve": "call run with --approve-budget CALLS",
                      **self.notes.warnings}
        if not self.repeats_given:
            rows = self._table()
            text += "; repeats table: " + "; ".join(_row_text(row) for row in rows)
            extra["repeats_table"] = rows
        return Stop("budget-unapproved", text, dict(self.shown) | data | extra)

    def _table(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for repeats in TABLE_REPEATS:
            try:
                plan = size_plan(self.contract, self.cases, repeats, self.options, self.cap)
            except Stop as error:
                rows.append({"repeats": repeats, "fits": False, "stop": str(error)})
                continue
            rows.append({"repeats": repeats, "fits": True, "holdout_calls": plan.holdout_calls, "search_calls": plan.metric_calls,
                         "calls": plan.sized.calls, "seconds": plan.sized.seconds})
        return rows

    def _fits(self, cap: int, repeats: int) -> bool:
        try:
            _ = size_plan(self.contract, self.cases, repeats, self.options, cap)
        except Stop:
            return False
        return True

    def _smallest(self, repeats: int) -> int | None:
        """Return the smallest number above the current cap, up to `JOURNEY_CALLS`, that fits. A journey run has none."""
        low, high = max(self.cap, APPROVED_CALLS) + 1, JOURNEY_CALLS
        if any(case.journey is not None for case in self.cases) or low > high or not self._fits(high, repeats):
            return None
        while low < high:
            middle = (low + high) // 2
            if self._fits(middle, repeats):
                high = middle
            else:
                low = middle + 1
        return low


def _row_text(row: dict[str, object]) -> str:
    if not row["fits"]:
        return f"repeats {row['repeats']} does not fit: {row['stop']}"
    return (f"repeats {row['repeats']}: {row['holdout_calls']} holdout gate calls, {row['search_calls']} search calls, "
            + f"{row['calls']} calls in all, {row['seconds']} seconds")
