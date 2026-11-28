"""Score baseline seed slots and check train and validation headroom."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

from skillz_experiments._budget import MAX_CONCURRENT_CALLS
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.gate import close
from skillz_experiments.run._outcomes import failed_outcome
from skillz_experiments.run._plan import repeat_indices
from skillz_experiments.run._reproduce import floor_cases
from skillz_experiments.run._signals import seed_signals
from skillz_experiments.run._state import RunState
from skillz_experiments.search import unit_score

# The headroom pass stops the run when a train or validation family has a seed mean at or above this value.
HEADROOM_CEILING = 0.9


def score_seed_slots(state: RunState, slots: Sequence[tuple[Case, str, int | None]]) -> None:
    """Score seed slots and checkpoint provider failures for resume."""
    seed = state.seed

    def score(slot: tuple[Case, str, int | None]) -> None:
        case, arm, repeat = slot
        try:
            _ = state.evaluate_case(seed, case, arm, holdout=case.split == "holdout", repeat=repeat)
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            with state.lock:
                state.outcomes.append(failed_outcome(case, seed, error, arm=arm, repeat=repeat))
                state.checkpoint()

    with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
        _ = list(pool.map(score, slots))


def saturated(mean: float) -> bool:
    return mean >= HEADROOM_CEILING or close(mean, HEADROOM_CEILING)


def seed_headroom(cases: Sequence[Case], found: Mapping[tuple[object, object, object], Mapping[str, object]],
                  reproduction: dict[str, dict[str, object]], expects_saturation: bool) -> dict[str, object]:
    """Apply the search headroom rule only to its train and validation seed slots."""
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
    easy = any(saturated(mean) for mean in means.values())
    signals = seed_signals(cases, found, reproduction, floor, expects_saturation)
    unscorable = bool(signals["unscorable"])
    stop = easy and not expects_saturation and not unscorable
    verdict = "unscorable" if unscorable else "too-easy" if stop else "ok"
    block: dict[str, object] = {"ceiling": HEADROOM_CEILING, "families": families, "verdict": verdict} | signals
    if reproduction:
        block["reproduction"] = reproduction
    return block
