"""Reproduction of regression cases: does the seed fail the case that a field failure produced?

A regression case reproduces when every scored seed repeat scores exactly 0 (fail-to-pass). Its `state` is
`fail-to-pass`, `pass-to-pass` when every scored seed repeat scores exactly 1.0, and `partial` otherwise (gh-663).
Only a pass-to-pass case is a floor case: it stays as a regression floor and out of the too-easy decision. A partial
case still has headroom, so it stays in its family mean.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from skillz_experiments._budget import MAX_CONCURRENT_CALLS
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.run._baseline import unmet_criteria
from skillz_experiments.run._outcomes import failed_outcome, holdout_unscored
from skillz_experiments.run._plan import repeat_indices
from skillz_experiments.run._state import RunState
from skillz_experiments.search import unit_score

# The arm of the one seed score of a holdout regression case. Search, reflection, the gate, and the claims never read it.
REPRODUCE_ARM = "reproduce"


def reproduce_outcomes(state: RunState) -> dict[str, dict[str, object]]:
    """Return the newest scored `reproduce` outcome of each holdout regression case."""
    with state.lock:
        return {str(item.get("case_id")): item for item in state.outcomes
                if item.get("arm") == REPRODUCE_ARM and not holdout_unscored(item)}


def score_holdout_regressions(state: RunState) -> None:
    """Score the seed once on each holdout regression case that has no scored `reproduce` outcome. A resume skips the done ones."""
    seed = state.seed
    done = reproduce_outcomes(state)
    todo = [case for case in state.cases_for("holdout") if case.regression and case.identifier not in done]

    def score(case: Case) -> None:
        try:
            _ = state.evaluate_case(seed, case, REPRODUCE_ARM, holdout=True)
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            with state.lock:
                state.outcomes.append(failed_outcome(case, seed, error, arm=REPRODUCE_ARM))
                state.checkpoint()

    with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
        _ = list(pool.map(score, todo))


def _reproduction(case: Case, scored: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Return the record of one regression case from its scored seed outcomes, in repeat order.

    A holdout case keeps only the level of each unmet criterion, so no judge text about a holdout case leaves the record.
    """
    record: dict[str, object] = {"split": case.split, "solvable": "unknown"}
    if not scored:
        none: dict[str, object] = {"scores": [], "reproduces": None, "reason": "no scored seed outcome"}
        return record | none
    scores = [unit_score(item["score"]) for item in scored]
    unmet: dict[str, dict[str, object]] = {}
    for item in scored:
        for name, value in unmet_criteria(dict(item)).items():
            if name not in unmet or cast(int, value["level"]) < cast(int, unmet[name]["level"]):
                unmet[name] = value
    if case.split == "holdout":
        unmet = {name: {"level": value["level"]} for name, value in unmet.items()}
    reproduces = all(score == 0.0 for score in scores)
    state = "fail-to-pass" if reproduces else "pass-to-pass" if all(score == 1.0 for score in scores) else "partial"
    return record | {"scores": scores, "reproduces": reproduces, "state": state, "unmet": unmet}


def reproductions(cases: Sequence[Case], seed_items: Mapping[tuple[object, object, object], dict[str, object]],
                  reproduced: Mapping[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """Return the reproduction record of each regression case in `cases`, by case id.

    `seed_items` holds the scored seed outcomes of the train and validation cases by (split, case, repeat).
    `reproduced` holds the scored `reproduce` outcome of each holdout case.
    """
    found: dict[str, dict[str, object]] = {}
    for case in cases:
        if not case.regression:
            continue
        if case.split == "holdout":
            scored = [reproduced[case.identifier]] if case.identifier in reproduced else []
        else:
            scored = [seed_items[key] for repeat in repeat_indices(case.split)
                      if (key := (case.split, case.identifier, repeat)) in seed_items]
        found[case.identifier] = _reproduction(case, scored)
    return found


def stop_if_unreproduced(state: RunState, reproduced: Mapping[str, object]) -> None:
    """Raise `holdout-faulted` when a holdout regression case has no scored `reproduce` outcome.

    The headroom pass then stays open, so a resume scores only the cases that failed.
    """
    lost = sorted(case.identifier for case in state.cases_for("holdout") if case.regression and case.identifier not in reproduced)
    if lost:
        raise CodedError("holdout-faulted", f"the seed evaluation of {len(lost)} holdout regression cases failed "
                         + f"({', '.join(lost)}); wait for the provider, then resume the run in the same directory")


def floor_cases(records: Mapping[str, Mapping[str, object]]) -> set[str]:
    """Return the ids of the floor cases: regression cases that the seed scores 1.0 on every scored repeat."""
    return {name for name, record in records.items() if record.get("state") == "pass-to-pass"}
