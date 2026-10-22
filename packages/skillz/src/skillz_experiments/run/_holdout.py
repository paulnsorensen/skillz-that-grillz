"""Holdout scoring shared by the gate and the replay, and the gate phase that scores baseline and winner.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import cast

from skillz_experiments._budget import MAX_CONCURRENT_CALLS, BudgetExhausted
from skillz_experiments._candidate import Candidate, candidate_files
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.evaluation import task_tokens
from skillz_experiments.gate import Verdict, holdout_deltas
from skillz_experiments.gate import verdict as gate_verdict
from skillz_experiments.run._outcomes import (
    by_repeat_order, failed_outcome, holdout_scores, holdout_unscored, is_failed, mean_tokens, reason_counts,
    rejection_detail, scored_holdout, timed_out,
)
from skillz_experiments.run._plan import case_calls
from skillz_experiments.run._record_fields import field, int_field
from skillz_experiments.run._state import RunState
from skillz_experiments.run._stop import Stop, baseline_rejected, check_timeout

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


def _holdout_todo(outcomes: Sequence[dict[str, object]], arms: Mapping[str, Candidate], holdout: Sequence[Case],
                  repeats: int) -> list[tuple[str, Case, int]]:
    """Return the (arm, case, repeat) slots of `arms` that have no scored holdout outcome yet."""
    done = {(item.get("arm"), item.get("case_id"), item.get("repeat")) for item in outcomes
            if item.get("split") == "holdout" and not holdout_unscored(item)}
    return [(arm, case, repeat) for arm in arms for case in holdout for repeat in range(repeats)
            if (arm, case.identifier, repeat) not in done]


def _holdout_need(state: RunState, todo: Sequence[tuple[str, Case, int]]) -> tuple[int, int]:
    """Return the calls that `todo` needs and the calls left in the approved budget after the replay reserve."""
    need = sum(case_calls(state.contract, case) for _, case, _ in todo)
    return need, state.budget.maximum - state.budget.calls - replay_need(state)


def _score_retained_baseline(state: RunState, holdout: Sequence[Case], repeats: int) -> dict[str, object]:
    """Score the baseline alone on the holdout when the search retained the seed, and return the gate fields.

    The verdict stays a tie, so no fault stops the run. A budget or deadline stop that leaves the baseline partly or
    fully unscored records `baseline_skipped` `budget-short` and keeps the scores that exist. A case that faults or
    ends in a check timeout stays unscored, and `baseline_skipped` is `faulted` when no case scored.
    """
    arms = {"baseline": state.seed}
    short = False
    try:
        _ = state.budget.remaining()
        todo = _holdout_todo(state.outcomes, arms, holdout, repeats)
        need, left = _holdout_need(state, todo)
        if left < need:
            return {"baseline_skipped": "budget-short"}
        score_holdout(state, [(state.seed, case, arm, repeat) for arm, case, repeat in todo])
    except BudgetExhausted:
        short = True
    scores = arm_scores(state.outcomes, "baseline")
    found: dict[str, object] = ({"baseline_scores": {case_id: math.fsum(each) / len(each) for case_id, each in scores.items()}}
                                if scores else {})
    if short:
        return {**found, "baseline_skipped": "budget-short"}
    if not scores:
        return {"baseline_skipped": "faulted"}
    return found


def run_gate(state: RunState, built: Callable[[Candidate], Candidate]) -> None:
    """Score baseline and winner on the unseen holdout, `repeats` times per case, then record the verdict."""
    holdout = state.cases_for("holdout")
    repeats = int_field(state.record, "repeats")
    seed, record, outcomes, statistics = state.seed, state.record, state.outcomes, state.statistics
    try:
        winner = built(seed.changed({name: candidate_files(field(record, "winner"))[name] for name in seed.editable}))
    except CodedError as error:
        raise Stop("build-failed", str(error), {"next": "the winner no longer builds; start a new run in a new directory"}) from None
    families = {case.identifier: case.family for case in holdout}
    if winner.identity == seed.identity:
        tie = gate_verdict({case.identifier: 0.0 for case in holdout}, thresholds=statistics, families=families)
        outcome = Verdict("inconclusive", 0.0, 0.0, tie.cases, ("winner-equals-baseline",), tie.skipped_families)
        record["gate"] = {**outcome.data(), "repeats": repeats, **_score_retained_baseline(state, holdout, repeats)}
    else:
        _ = state.budget.remaining()
        rejected = next((item for item in outcomes if item.get("arm") == "baseline"
                         and item.get("status") == "candidate-contract-rejected" and not timed_out(item)), None)
        if rejected is not None:
            raise baseline_rejected(rejection_detail(rejected))
        arms = {"baseline": seed, "winner": winner}
        todo = _holdout_todo(outcomes, arms, holdout, repeats)

        def inspect(arm: str, result: dict[str, object]) -> None:
            if timed_out(result):
                raise check_timeout(arm, rejection_detail(result))
            if arm == "baseline" and result.get("status") == "candidate-contract-rejected":
                raise baseline_rejected(rejection_detail(result))

        need, left = _holdout_need(state, todo)
        if left < need:
            raise Stop("gate-budget-exhausted", f"the holdout gate needs {need} more calls; "
                       + f"{left} remain in the approved budget after the replay reserve",
                       {"next": "start a new run in a new directory"})
        score_holdout(state, [(arms[arm], case, arm, repeat) for arm, case, repeat in todo], inspect=inspect)
        paired = holdout_deltas(arm_scores(outcomes, "baseline"), arm_scores(outcomes, "winner"))
        lost = [item for item in outcomes if item.get("split") == "holdout" and item.get("arm") in arms and is_failed(item)]
        if lost and not paired.deltas:
            raise CodedError("holdout-faulted", f"no holdout case has a scored baseline and winner pair; {len(lost)} holdout "
                             + f"evaluations failed ({reason_counts(lost)}); wait for the provider, then resume the run")
        tokens = ({case_id: arms_tokens for case_id, arms_tokens in holdout_tokens(outcomes).items()
                   if case_id in paired.deltas} if statistics.token_rule else None)
        outcome = replace(gate_verdict(paired.deltas, thresholds=statistics, families=families,
                                       tokens=tokens, se_floor=paired.se_floor,
                                       planned=len(holdout)), moves=paired.moves)
        record["gate"] = {**outcome.data(), "repeats": repeats}
    record["winner_hash"] = winner.identity
    record["phase"] = "gated"
    state.checkpoint()
