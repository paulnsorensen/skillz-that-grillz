"""Reproduction of regression cases: does the seed fail the case that a field failure produced?

A regression case reproduces when every scored seed repeat scores exactly 0 (fail-to-pass). Its `state` is
`fail-to-pass`, `pass-to-pass` when every scored seed repeat scores exactly 1.0, and `partial` otherwise (gh-663).
Only a pass-to-pass case is a floor case: it stays as a regression floor and out of the too-easy decision. A partial
case still has headroom, so it stays in its family mean.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from skillz_experiments._cases import Case
from skillz_experiments.run._baseline import unmet_criteria
from skillz_experiments.search import unit_score

def reproduction_entry(case: Case, scored: Sequence[Mapping[str, object]]) -> dict[str, object]:
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


def floor_cases(records: Mapping[str, Mapping[str, object]]) -> set[str]:
    """Return the ids of the floor cases: regression cases that the seed scores 1.0 on every scored repeat."""
    return {name for name, record in records.items() if record.get("state") == "pass-to-pass"}
