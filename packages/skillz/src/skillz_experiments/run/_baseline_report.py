"""Write the private report of the completed seed measurement."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from statistics import mean

from skillz_experiments._cases import Case
from skillz_experiments._records import write
from skillz_experiments.run._baseline import NOT_RUN, REPORT_NAME, unmet_criteria
from skillz_experiments.run._export import criteria_flags, usage_by_role, zero_cases
from skillz_experiments.run._headroom import saturated
from skillz_experiments.search import unit_score


def _families(cases: Sequence[Case], scores: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, list[float]]] = {}
    for case in cases:
        grouped.setdefault(case.split, {}).setdefault(case.family, []).append(mean(scores[case.identifier]))
    return {split: {family: mean(values) for family, values in families.items()} for split, families in grouped.items()}


def _diagnostics(measured: dict[str, list[dict[str, object]]], seed: str) -> dict[str, object]:
    # The train repeats have distinct storage arms but measure the same seed as one pair.
    rows = [{key: value for key, value in item.items() if key != "search_candidate"}
            | {"arm": "baseline", "candidate_hash": seed} for items in measured.values() for item in items]
    return {"criteria_flags": criteria_flags(rows), "zero_cases": zero_cases(rows)}


def write_baseline_report(out: Path, record: dict[str, object], cases: Sequence[Case],
                          measured: dict[str, list[dict[str, object]]], seed: str) -> dict[str, object]:
    scores = {name: [unit_score(item["score"]) for item in items] for name, items in measured.items()}
    families = _families(cases, scores)
    report: dict[str, object] = NOT_RUN | {
        "sharing": "private-local-only",
        "exposed": record["exposed"],
        "cases": {case.identifier: {"split": case.split, "family": case.family, "scores": scores[case.identifier],
                                   "unmet": [unmet_criteria(item) for item in measured[case.identifier]]}
                  for case in cases},
        "family_means": families,
        "saturated_families": {split: [name for name, score in sorted(families.get(split, {}).items()) if saturated(score)]
                              for split in ("train", "validation")},
        "calls_by_role": record.get("calls_by_role"),
        "seconds_by_role": record.get("seconds_by_role"),
        "usage_by_role": usage_by_role(record),
    } | _diagnostics(measured, seed)
    write(out / REPORT_NAME, report)
    return report
