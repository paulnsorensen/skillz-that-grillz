"""The headroom pass: score the seed once on every train and validation case, and stop when a family is saturated.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from typing import cast

from skillz_experiments._budget import MAX_CONCURRENT_CALLS
from skillz_experiments._cases import Case, CodedError, Split, mapping
from skillz_experiments.gate import close
from skillz_experiments.run._outcomes import failed_outcome, is_failed
from skillz_experiments.run._search_phase import SearchPhase
from skillz_experiments.run._state import RunState
from skillz_experiments.search import unit_score

# The headroom pass stops the run when a train or validation family has a seed mean at or above this value.
HEADROOM_CEILING = 0.9


def too_easy_message(headroom: Mapping[str, object]) -> str:
    """Return the `cases-too-easy` stop text from a recorded headroom block.

    The text comes from the block alone, so a resume gets the same stop when the run died before it recorded `failure`.
    """
    ceiling = cast(float, headroom["ceiling"])
    families = [(name, mapping(item)) for name, item in mapping(headroom["families"]).items()]
    named = "; ".join(f"{name} ({item['split']}) mean {cast(float, item['mean']):.2f}" for name, item in families
                      if cast(float, item["mean"]) >= ceiling or close(cast(float, item["mean"]), ceiling))
    return (f"the seed already scores at least {ceiling} on {named}; a search cannot show a gain on cases this easy; "
            + "draft harder cases in a new run directory, then call run again")


def run_headroom(state: RunState, search: SearchPhase) -> None:
    """Score the seed once on every train and validation case. Stop with `cases-too-easy` when a family is saturated.

    Each score is a normal search outcome of the seed, so `SearchPhase` serves it to the search once. The validation
    outcomes stand in for the seed validation pass of GEPA. Only the train outcomes add to the search ceiling, so
    the search keeps the room it had after its own seed pass. A resume scores only the cases that
    have no scored seed outcome. A run whose statistics set `min_token_saving` expects saturated cases:
    it records the scores and does not stop.
    """
    if isinstance(state.record.get("headroom"), dict):
        return
    seed = state.seed
    cases = [case for split in ("train", "validation") for case in state.cases_for(cast(Split, split))]

    def seed_scores() -> dict[tuple[object, object], float]:
        with state.lock:
            return {(item["split"], item["case_id"]): unit_score(item["score"]) for item in search.search_outcomes()
                    if item.get("search_candidate") == seed.identity and not is_failed(item)}

    def score(case: Case) -> None:
        try:
            _ = state.evaluate_case(seed, case, "search")
        except CodedError:
            raise
        except (OSError, ValueError, RuntimeError) as error:
            with state.lock:
                state.outcomes.append(failed_outcome(case, seed, error))
                state.checkpoint()

    search.mark_start()
    found = seed_scores()
    with ThreadPoolExecutor(MAX_CONCURRENT_CALLS) as pool:
        _ = list(pool.map(score, [case for case in cases if (case.split, case.identifier) not in found]))
    search.stop_if_faulted()
    found = seed_scores()
    by_family: dict[str, list[float]] = {}
    splits: dict[str, str] = {}
    for case in cases:
        splits[case.family] = case.split
        if (case.split, case.identifier) in found:
            by_family.setdefault(case.family, []).append(found[(case.split, case.identifier)])
    means = {name: sum(scores) / len(scores) for name, scores in sorted(by_family.items())}
    families = {name: {"split": splits[name], "mean": means[name], "cases": len(by_family[name])} for name in means}
    easy = {name: mean for name, mean in means.items() if mean >= HEADROOM_CEILING or close(mean, HEADROOM_CEILING)}
    stop = bool(easy) and state.statistics.min_token_saving is None
    block: dict[str, object] = {"ceiling": HEADROOM_CEILING, "families": families, "verdict": "too-easy" if stop else "ok"}
    state.record["headroom"] = block
    state.checkpoint()
    if stop:
        raise CodedError("cases-too-easy", too_easy_message(block))
