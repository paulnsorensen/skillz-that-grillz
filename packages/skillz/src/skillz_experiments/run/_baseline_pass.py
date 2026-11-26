"""Score and checkpoint the seed slots that a baseline report needs."""
from __future__ import annotations

from collections.abc import Sequence

from skillz_experiments._cases import Case, CodedError, Split
from skillz_experiments.run._baseline_report import write_baseline_report
from skillz_experiments.run._headroom import score_seed_slots, seed_headroom
from skillz_experiments.run._outcomes import holdout_unscored, is_failed
from skillz_experiments.run._reproduce import reproduction_entry
from skillz_experiments.run._state import RunState

_SPLITS: tuple[Split, ...] = ("train", "validation", "holdout")


def _slots(case: Case) -> tuple[tuple[str, int | None], tuple[str, int]]:
    if case.split == "train":
        return ("search", None), ("baseline", 1)
    arm = "baseline" if case.split == "holdout" else "search"
    return (arm, 0), (arm, 1)


def _scored(state: RunState) -> dict[tuple[object, object, object], dict[str, object]]:
    with state.lock:
        return {(item.get("arm"), item.get("case_id"), item.get("repeat")): item for item in state.outcomes
                if (item.get("arm") == "search" and item.get("search_candidate") == state.seed.identity
                    and not is_failed(item))
                or (item.get("arm") == "baseline" and not holdout_unscored(item))}


def _headroom(state: RunState, cases: Sequence[Case],
              measured: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    search_cases = [case for case in cases if case.split != "holdout"]
    search_items: dict[tuple[object, object, object], dict[str, object]] = {}
    reproduction: dict[str, dict[str, object]] = {}
    for case in cases:
        items = measured[case.identifier]
        reused = [item for item in items if item["arm"] == "search"]
        for item in reused:
            search_items[case.split, case.identifier, item.get("repeat")] = item
        if case.regression:
            reproduction[case.identifier] = reproduction_entry(case, items if case.split == "holdout" else reused)
    return seed_headroom(search_cases, search_items, reproduction, state.statistics.min_token_saving is not None)


def measure_baseline(state: RunState) -> dict[str, object]:
    """Resume missing seed slots, then publish the report before checkpointing headroom as complete."""
    cases = [case for split in _SPLITS for case in state.cases_for(split)]
    scored = _scored(state)
    score_seed_slots(state, [(case, arm, repeat) for case in cases for arm, repeat in _slots(case)
                             if (arm, case.identifier, repeat) not in scored])
    scored = _scored(state)
    measured = {case.identifier: [scored[key] for arm, repeat in _slots(case)
                                  if (key := (arm, case.identifier, repeat)) in scored] for case in cases}
    state.record["exposed"] = sorted(name for name, items in measured.items() if items)
    missing = sum(2 - len(items) for items in measured.values())
    if missing:
        state.checkpoint()
        raise CodedError("baseline-faulted", f"{missing} seed evaluations have no score; resume in the same run directory")
    headroom = _headroom(state, cases, measured)
    report = write_baseline_report(state.out, state.record, cases, measured, state.seed.identity)
    state.record["headroom"] = headroom
    state.checkpoint()
    return report
