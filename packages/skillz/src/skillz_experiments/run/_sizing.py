"""Size a run against the approved call number, and show the repeats table when it does not fit (ADR-005, gh-588).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from skillz_experiments._budget import APPROVED_CALLS, JOURNEY_CALLS, MAX_CONCURRENT_CALLS, Budget, estimate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import Contract
from skillz_experiments._intake import IntakeCase
from skillz_experiments.harness import ClaudeOptions
from skillz_experiments.run._edit_plan import ReflectionNotes
from skillz_experiments.run._plan import (MINIMUM_CANDIDATES, PREFLIGHT_CALLS, Plan, case_calls, command_seconds,
                                          plan_estimate, size_plan)
from skillz_experiments.run._stop import Stop

TABLE_REPEATS = (1, 2, 3)


def baseline_plan(contract: Contract, cases: Sequence[Case | IntakeCase], options: ClaudeOptions, cap: int,
                  preflight: int = PREFLIGHT_CALLS) -> Plan:
    """Size two seed evaluations per scored case and no search, gate, or replay."""
    sandbox = command_seconds(contract, cases, options)
    sandbox_total = math.ceil(2 * len(cases) * sandbox / MAX_CONCURRENT_CALLS)
    sized = estimate(preflight_calls=preflight, search_calls=2 * sum(case_calls(contract, case) for case in cases),
                     journey=any(case.journey is not None for case in cases), cap=cap)
    return Plan(0, 0, 0, 0, type(sized)(sized.calls, sized.seconds + sandbox_total, sized.journey, cap),
                sandbox_total=sandbox_total, sandbox_seconds=sandbox)


def budget_data(budget: Budget) -> dict[str, object]:
    return {"maximum": budget.maximum, "seconds": budget.seconds, "reserve": budget.reserve,
            "reserve_seconds": budget.reserve_seconds, "approved": budget.approved,
            **({"journey": True} if budget.journey else {}), **({"cap": budget.cap} if budget.cap else {})}


def raised_cap(approve_budget: int | None, cases: Sequence[Case | IntakeCase], *, flag: str = "--approve-budget") -> int:
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
    preflight: int = PREFLIGHT_CALLS

    def estimate(self, plan: Plan, repeats: int) -> dict[str, object]:
        """Return the shown estimate of `plan`: the `plan_estimate` that `verify_budget` checks, the expected candidates,
        the gate reserve, and the reflection block."""
        return plan_estimate(plan, repeats, self.holdout_cases) | {
            "expected_candidates": plan.candidates, "reserve": plan.reserve,
            "reflection": self.notes.time(plan.reflection_calls, plan.sized)}

    def plan(self, repeats: int) -> Plan:
        """Return the plan for `repeats`. A plan that does not fit stops with the smallest approval and the repeats table.

        When an approval fits but none up to `JOURNEY_CALLS` buys `MINIMUM_CANDIDATES`, the stop is `search-underpowered`.
        """
        try:
            return size_plan(self.contract, self.cases, repeats, self.options, self.cap, self.preflight)
        except Stop as error:
            fitting = self._smallest(repeats)
            if fitting is not None and self._smallest(repeats, _buys_floor) is None:
                plan = size_plan(self.contract, self.cases, repeats, self.options, fitting, self.preflight)
                raise self._underpowered(plan, repeats, None) from None
            raise self.stop(str(error), {}, repeats, fitting=True) from None

    def stop(self, message: str, data: dict[str, object], repeats: int, *, fitting: bool = False) -> Stop:
        """Return the `budget-unapproved` stop. It adds the repeats table unless the author gave `--repeats`.

        With `fitting`, it also names the smallest approved number that fits, when one up to `JOURNEY_CALLS` does, and
        shows the `estimate` of the plan at that number, so the user sees what the approval covers. A number whose plan
        also buys `MINIMUM_CANDIDATES` comes first, so the named approval passes the candidate floor when one can.
        """
        text, extra = message, dict[str, object]()
        smallest = (self._smallest(repeats, _buys_floor) or self._smallest(repeats)) if fitting else None
        if smallest is not None:
            text += f"; --approve-budget {smallest} is the smallest approved number that fits"
            shown = self.estimate(size_plan(self.contract, self.cases, repeats, self.options, smallest, self.preflight), repeats)
            extra |= {"smallest_approval": smallest, "estimate": shown, "approve": "call run with --approve-budget CALLS",
                      **self.notes.warnings}
        if not self.repeats_given:
            rows = self._table()
            text += "; repeats table: " + "; ".join(_row_text(row) for row in rows)
            extra["repeats_table"] = rows
        return Stop("budget-unapproved", text, dict(self.shown) | data | extra)

    def refuse_underpowered(self, plan: Plan, repeats: int) -> None:
        """Stop with `search-underpowered` when `plan` buys fewer than `MINIMUM_CANDIDATES` proposals. No model call runs."""
        if not _buys_floor(plan):
            raise self._underpowered(plan, repeats, self._smallest(repeats, _buys_floor))

    def _underpowered(self, plan: Plan, repeats: int, smallest: int | None) -> Stop:
        """Return the `search-underpowered` stop of `plan`. It names `smallest`, the smallest approved number that buys
        `MINIMUM_CANDIDATES`, or states that no number up to `JOURNEY_CALLS` (the raised-cap limit and the journey
        ceiling) does.
        """
        if smallest is None:
            advice = (f"no approved number up to {JOURNEY_CALLS} calls buys {MINIMUM_CANDIDATES}; "
                      + "lower --repeats, or draft fewer validation and holdout cases in a new run directory")
            step = advice
        else:
            advice = f"--approve-budget {smallest} is the smallest approved number that buys {MINIMUM_CANDIDATES}"
            step = f"call run --search with --approve-budget {smallest}"
        return Stop("search-underpowered", f"the search plan buys {plan.candidates} candidates, fewer than "
                    + f"{MINIMUM_CANDIDATES}; {advice}", dict(self.shown) | {
                        "expected_candidates": plan.candidates, "minimum_candidates": MINIMUM_CANDIDATES,
                        "smallest_approval": smallest, "estimate": self.estimate(plan, repeats), "next": step})

    def _table(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for repeats in TABLE_REPEATS:
            try:
                plan = size_plan(self.contract, self.cases, repeats, self.options, self.cap, self.preflight)
            except Stop as error:
                rows.append({"repeats": repeats, "fits": False, "stop": str(error)})
                continue
            rows.append({"repeats": repeats, "fits": True, "holdout_calls": plan.holdout_calls, "search_calls": plan.metric_calls,
                         "calls": plan.sized.calls, "seconds": plan.sized.seconds})
        return rows

    def _fits(self, cap: int, repeats: int, enough: Callable[[Plan], bool]) -> bool:
        try:
            plan = size_plan(self.contract, self.cases, repeats, self.options, cap, self.preflight)
        except Stop:
            return False
        return enough(plan)

    def _smallest(self, repeats: int, enough: Callable[[Plan], bool] = lambda _plan: True) -> int | None:
        """Return the smallest number above the current cap, up to `JOURNEY_CALLS`, whose plan fits and is `enough`.

        A journey run has none. A larger cap never shrinks the plan, so the search is a bisection.
        """
        low, high = max(self.cap, APPROVED_CALLS) + 1, JOURNEY_CALLS
        if any(case.journey is not None for case in self.cases) or low > high or not self._fits(high, repeats, enough):
            return None
        while low < high:
            middle = (low + high) // 2
            if self._fits(middle, repeats, enough):
                high = middle
            else:
                low = middle + 1
        return low


def _buys_floor(plan: Plan) -> bool:
    return plan.candidates >= MINIMUM_CANDIDATES


def _row_text(row: dict[str, object]) -> str:
    if not row["fits"]:
        return f"repeats {row['repeats']} does not fit: {row['stop']}"
    return (f"repeats {row['repeats']}: {row['holdout_calls']} holdout gate calls, {row['search_calls']} search calls, "
            + f"{row['calls']} calls in all, {row['seconds']} seconds")
