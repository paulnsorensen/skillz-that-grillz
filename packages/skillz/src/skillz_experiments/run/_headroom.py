"""The headroom pass: score the seed on every train and validation run, and stop when a family is saturated.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from skillz_experiments._budget import MAX_CONCURRENT_CALLS
from skillz_experiments._cases import Case, CodedError, Split
from skillz_experiments.gate import close
from skillz_experiments.run._baseline import too_easy_stop
from skillz_experiments.run._outcomes import failed_outcome, is_failed
from skillz_experiments.run._plan import repeat_indices
from skillz_experiments.run._reproduce import (
    floor_cases, reproduce_outcomes, reproductions, score_holdout_regressions, stop_if_unreproduced,
)
from skillz_experiments.run._search_phase import SearchPhase
from skillz_experiments.run._signals import seed_signals, unscorable_stop, warn
from skillz_experiments.run._state import RunState
from skillz_experiments.search import unit_score

# The headroom pass stops the run when a train or validation family has a seed mean at or above this value.
HEADROOM_CEILING = 0.9


def run_headroom(state: RunState, search: SearchPhase) -> None:
    """Score the seed once on every train case and once per repeat index on every validation case.

    Stop with `cases-too-easy` when a family is saturated. The stop data holds the baseline report.
    Each score is a normal search outcome of the seed, so `SearchPhase` serves it to the search once. The validation
    outcomes stand in for the seed validation pass of GEPA. Only the train outcomes add to the search ceiling, so
    the search keeps the room it had after its own seed pass. A resume scores only the runs that have no scored seed
    outcome, so a case with one repeat missing counts as missing and scores that repeat. A case scores the mean of its
    scored repeats. A regression case adds its reproduction to the block: the seed scores, whether every one is 0, its
    state, and the unmet criteria. Each holdout regression case scores once under the `reproduce` arm. A floor case,
    one that the seed scores 1.0 on every repeat, stays out of its family mean; a partial case stays in it.
    A run whose statistics set `min_token_saving` expects saturated cases: it records the scores and does not stop.
    The block also records each case with its marks, the saturated counts, and the warnings (`run/_signals.py`). A case
    that scores 0 on every repeat through a setup fault stops the run with `cases-unscorable`.
    """
    if isinstance(state.record.get("headroom"), dict):
        return
    seed = state.seed
    cases = [case for split in ("train", "validation") for case in state.cases_for(cast(Split, split))]
    runs = [(case, repeat) for case in cases for repeat in repeat_indices(case.split)]

    def seed_items() -> dict[tuple[object, object, object], dict[str, object]]:
        with state.lock:
            return {(item["split"], item["case_id"], item.get("repeat")): item for item in search.search_outcomes()
                    if item.get("search_candidate") == seed.identity and not is_failed(item)}

    def score(run: tuple[Case, int | None]) -> None:
        case, repeat = run
        try:
            _ = state.evaluate_case(seed, case, "search", repeat=repeat)
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            with state.lock:
                state.outcomes.append(failed_outcome(case, seed, error, repeat=repeat))
                state.checkpoint()

    search.mark_start()
    found = seed_items()
    with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
        _ = list(pool.map(score, [(case, repeat) for case, repeat in runs
                                  if (case.split, case.identifier, repeat) not in found]))
    score_holdout_regressions(state)
    search.stop_if_faulted()
    reproduced = reproduce_outcomes(state)
    stop_if_unreproduced(state, reproduced)
    found = seed_items()
    reproduction = reproductions([*cases, *state.cases_for("holdout")], found, reproduced)
    floor = floor_cases(reproduction)
    by_family: dict[str, list[float]] = {}
    splits: dict[str, str] = {}
    for case in cases:
        splits[case.family] = case.split
        scored = [unit_score(found[key]["score"]) for repeat in repeat_indices(case.split)
                  if (key := (case.split, case.identifier, repeat)) in found]
        if scored and case.identifier not in floor:
            by_family.setdefault(case.family, []).append(math.fsum(scored) / len(scored))
    means = {name: sum(scores) / len(scores) for name, scores in sorted(by_family.items())}
    families = {name: {"split": splits[name], "mean": means[name], "cases": len(by_family[name])} for name in means}
    easy = {name: mean for name, mean in means.items() if mean >= HEADROOM_CEILING or close(mean, HEADROOM_CEILING)}
    signals = seed_signals(cases, found, reproduction, floor, state.statistics.min_token_saving is not None)
    unscorable = bool(signals["unscorable"])
    stop = bool(easy) and state.statistics.min_token_saving is None and not unscorable
    verdict = "unscorable" if unscorable else "too-easy" if stop else "ok"
    block: dict[str, object] = {"ceiling": HEADROOM_CEILING, "families": families, "verdict": verdict} | signals
    if reproduction:
        block["reproduction"] = reproduction
    state.record["headroom"] = block
    state.checkpoint()
    warn(cast(list[str], signals["warnings"]))
    if unscorable:
        raise unscorable_stop(block)
    if stop:
        raise too_easy_stop(state.record, state.out)
