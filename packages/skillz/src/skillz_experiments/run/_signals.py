"""Per-case seed signals of the headroom pass: the case record, the two marks, the unscorable stop, and the warnings.

A case is `saturated` when every seed repeat scores exactly 1.0, and `suspect-zero` when every seed repeat scores 0.
A case whose every repeat scores 0 with a setup fault cannot teach the search, so the run stops, even for a
`fail-to-pass` regression case. Any other suspect-zero case only warns. A train case has headroom when it has a seed
score and is not saturated, so a train split with fewer than `REFLECTION_MINIBATCH` such cases also warns.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import cast

from skillz_experiments._cases import Case, mapping
from skillz_experiments.run._plan import repeat_indices
from skillz_experiments.run._stop import Stop
from skillz_experiments.search import REFLECTION_MINIBATCH, unit_score

# A gate fault or an outcome status that shows a broken setup, not a verdict of the gate or the judge.
SETUP_FAULTS = frozenset({"timeout", "invalid-output"})
SETUP_STATUSES = frozenset({"activation-rejected", "capture-failed"})
DIAGNOSTIC_CUT = 200

_NEXT = ("fix the gate, the capture, or the case so that its seed run reaches a verdict, then start a new run in a "
         + "new directory and ask the case question again")


def _setup_fault(fault: object, status: object) -> str | None:
    if fault in SETUP_FAULTS:
        return cast(str, fault)
    return cast(str, status) if status in SETUP_STATUSES else None


def _entry(case: Case, items: Sequence[Mapping[str, object]], state: object) -> dict[str, object]:
    scores = [unit_score(item["score"]) for item in items]
    mark = None
    if scores and all(score == 1.0 for score in scores):
        mark = "saturated"
    elif scores and all(score == 0.0 for score in scores) and state != "fail-to-pass":
        mark = "suspect-zero"
    return {"split": case.split, "family": case.family, "scores": scores, "mark": mark,
            "status": [item.get("status") for item in items], "gate_fault": [item.get("gate_fault") for item in items]}


def _faults(entry: Mapping[str, object]) -> list[str | None]:
    """Return the setup fault of each repeat of `entry`, None for a repeat that reached a verdict."""
    return [_setup_fault(fault, status) for fault, status in zip(
        cast(list[object], entry["gate_fault"]), cast(list[object], entry["status"]), strict=True)]


def _cause(faults: Sequence[str | None], items: Sequence[Mapping[str, object]]) -> str:
    named = sorted({fault for fault in faults if fault is not None})
    diagnostic = next((str(item["gate_diagnostic"]).strip() for item in items if item.get("gate_diagnostic")), "")
    text = f"setup fault {', '.join(named)}" if named else "the gate or the judge scored 0"
    return text + (f" ({diagnostic[:DIAGNOSTIC_CUT]})" if diagnostic else "")


def _counts(entries: Mapping[str, Mapping[str, object]], key: str) -> dict[str, int]:
    """Return the saturated case count for each value of `key`, 0 included."""
    names = {str(entry[key]) for entry in entries.values()}
    return {name: sum(entry[key] == name and entry["mark"] == "saturated" for entry in entries.values())
            for name in sorted(names)}


def seed_signals(cases: Sequence[Case], found: Mapping[tuple[object, object, object], Mapping[str, object]],
                 reproduction: Mapping[str, Mapping[str, object]], floor: set[str],
                 expects_saturation: bool) -> dict[str, object]:
    """Return the `cases`, `saturated`, `unscorable`, and `warnings` entries of the headroom block.

    `found` holds the scored seed outcome of each (split, case, repeat). A `fail-to-pass` regression case is never
    suspect-zero, but it is unscorable when a setup fault zeroes every repeat. A run that expects saturated cases
    (`min_token_saving`) gets no saturation warning.
    """
    entries: dict[str, dict[str, object]] = {}
    unscorable: list[dict[str, object]] = []
    warnings: list[str] = []
    for case in cases:
        items = [found[key] for repeat in repeat_indices(case.split)
                 if (key := (case.split, case.identifier, repeat)) in found]
        entry = _entry(case, items, reproduction.get(case.identifier, {}).get("state"))
        entries[case.identifier] = entry
        scores = cast(list[float], entry["scores"])
        if not scores or any(score != 0.0 for score in scores):
            continue
        faults = _faults(entry)
        cause = _cause(faults, items)
        if all(fault is not None for fault in faults):
            unscorable.append({"case_id": case.identifier, "split": case.split, "family": case.family, "cause": cause})
        elif entry["mark"] == "suspect-zero":
            warnings.append(f"suspect-zero: the seed scores 0 on every repeat of {case.identifier} ({case.split}, "
                            + f"family {case.family}); {cause}; the case may be unsolvable or its gate wrong")
    train = [identifier for identifier, entry in entries.items() if entry["split"] == "train"]
    open_train = [identifier for identifier in train if entries[identifier]["scores"]
                  and entries[identifier]["mark"] != "saturated" and identifier not in floor]
    if len(open_train) < REFLECTION_MINIBATCH and not expects_saturation:
        warnings.append(f"saturation: {len(open_train)} of {len(train)} train cases have headroom, fewer than "
                        + f"{REFLECTION_MINIBATCH}, so the search has little to learn from; add harder train cases")
    return {"cases": entries, "saturated": {"families": _counts(entries, "family"), "splits": _counts(entries, "split")},
            "unscorable": unscorable, "warnings": warnings}


def warn(texts: Sequence[str]) -> None:
    """Print each warning to stderr in the style of the pre-search warnings."""
    for text in texts:
        print(f"skillz-experiment: warning: {text}", file=sys.stderr)


def unscorable_stop(headroom: Mapping[str, object]) -> Stop:
    """Return the `cases-unscorable` stop of a recorded headroom block. The text comes from the block alone."""
    cases = [mapping(item) for item in cast(list[object], headroom["unscorable"])]
    named = "; ".join(f"{item['case_id']} ({item['split']}, family {item['family']}): {item['cause']}" for item in cases)
    return Stop("cases-unscorable", f"the seed scores 0 on every repeat of {len(cases)} case(s) because of a setup fault, "
                + f"not a verdict: {named}; a search cannot learn from them",
                {"next": _NEXT, "cases": cases})
