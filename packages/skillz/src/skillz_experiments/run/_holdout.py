"""Holdout scoring shared by the gate and the replay: the tasks, the newest outcomes, and the call need.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from skillz_experiments._budget import MAX_CONCURRENT_CALLS, BudgetExhausted
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.evaluation import task_tokens
from skillz_experiments.run._outcomes import (
    by_repeat_order, failed_outcome, holdout_scores, holdout_unscored, scored_holdout,
)
from skillz_experiments.run._plan import case_calls
from skillz_experiments.run._stall import mean_tokens
from skillz_experiments.run._state import RunState

Inspect = Callable[[str, dict[str, object]], None]


def arm_scores(outcomes: Sequence[Mapping[str, object]], arm: str) -> dict[str, list[float]]:
    """Return the scored holdout scores of `arm` as a list per case, in repeat order."""
    return by_repeat_order(holdout_scores(outcomes, arm))


def holdout_tokens(outcomes: Sequence[dict[str, object]]) -> dict[str, tuple[float | None, float | None]]:
    """Return each holdout case's mean task tokens per arm over its repeats. A missing count makes the mean None.

    The newest outcome of an (arm, case, repeat) counts.
    """
    newest: dict[tuple[object, object, object], dict[str, object]] = {}
    for item in scored_holdout(outcomes, ("baseline", "winner")):
        newest[(item["arm"], item["case_id"], item.get("repeat"))] = item
    counts: dict[str, dict[str, list[int | None]]] = {}
    for item in newest.values():
        counts.setdefault(cast(str, item["case_id"]), {"baseline": [], "winner": []})[
            cast(str, item["arm"])].append(task_tokens(item))
    return {case_id: (mean_tokens(arms["baseline"]), mean_tokens(arms["winner"]))
            for case_id, arms in counts.items()}


def replay_outcomes(state: RunState) -> dict[str, dict[str, object]]:
    """Return the newest scored replay outcome of each holdout case.

    An `evaluation-failed` outcome and a final check timeout are unscored.
    """
    with state.lock:
        return {cast(str, item["case_id"]): item for item in state.outcomes
                if item.get("arm") == "replay" and item.get("split") == "holdout" and not holdout_unscored(item)}


def replay_need(state: RunState) -> int:
    """Return the calls that the replay still needs: the cost of each holdout case without a scored replay outcome."""
    done = replay_outcomes(state)
    return sum(case_calls(state.contract, case) for case in state.cases_for("holdout") if case.identifier not in done)


def score_holdout(state: RunState, tasks: Sequence[tuple[Candidate, Case, str, int]], *,
                  inspect: Inspect | None = None, keep_budget_stop: bool = False) -> None:
    """Evaluate each (candidate, case, arm, repeat) task on the holdout. A plain failure becomes a failed outcome.

    A CodedError ends the work. `inspect` sees the arm and the result of each task that returns one.
    With `keep_budget_stop`, a budget stop first records a failed outcome, so the calls that it charged keep an outcome.
    """
    def record(case: Case, candidate: Candidate, error: Exception, arm: str, repeat: int) -> None:
        with state.lock:
            state.outcomes.append(failed_outcome(case, candidate, error, arm=arm, repeat=repeat))
            state.checkpoint()

    def score(task: tuple[Candidate, Case, str, int]) -> None:
        candidate, case, arm, repeat = task
        latched = state.faults.latched
        try:
            result = state.evaluate_case(candidate, case, arm, holdout=True, repeat=repeat)
        except BudgetExhausted as error:
            # A task that meets an already latched stop made no call, so it keeps no outcome.
            if keep_budget_stop and not latched:
                record(case, candidate, error, arm, repeat)
            raise
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            record(case, candidate, error, arm, repeat)
            return
        if inspect is not None:
            inspect(arm, result)

    with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
        _ = list(pool.map(score, tasks))
