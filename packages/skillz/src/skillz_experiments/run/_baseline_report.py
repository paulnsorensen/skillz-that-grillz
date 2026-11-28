"""Write the private report of the completed seed measurement."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import mean
from typing import cast

from skillz_experiments._cases import Case, mapping
from skillz_experiments._records import write
from skillz_experiments.run._baseline import NOT_RUN, REPORT_NAME, unmet_criteria
from skillz_experiments.run._export import criteria_flags, usage_by_role, zero_cases
from skillz_experiments.run._headroom import saturated
from skillz_experiments.run._record_fields import field, int_field
from skillz_experiments.search import unit_score


def _family_means(cases: Sequence[Case], scores: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """Return the family means of each split: the mean of the case means, over every case and both measured repeats."""
    grouped: dict[str, dict[str, list[float]]] = {"train": {}, "validation": {}, "holdout": {}}
    for case in cases:
        grouped[case.split].setdefault(case.family, []).append(mean(scores[case.identifier]))
    return {split: {family: mean(values) for family, values in families.items()} for split, families in grouped.items()}


def _saturated(headroom: Mapping[str, object]) -> dict[str, list[str]]:
    """Return the train and validation families that the `--search` too-easy rule calls saturated.

    The headroom block holds that rule: the seed repeats of the search slots, and no floor case.
    """
    block = {name: mapping(entry) for name, entry in mapping(headroom["families"]).items()}
    return {split: sorted(name for name, entry in block.items()
                          if entry["split"] == split and saturated(cast(float, entry["mean"])))
            for split in ("train", "validation")}


def _diagnostics(measured: dict[str, list[dict[str, object]]], seed: str) -> dict[str, object]:
    # The train repeats have distinct storage arms but measure the same seed as one pair.
    rows = [{key: value for key, value in item.items() if key != "search_candidate"}
            | {"arm": "baseline", "candidate_hash": seed} for items in measured.values() for item in items]
    return {"criteria_flags": criteria_flags(rows), "zero_cases": zero_cases(rows)}


def write_baseline_report(out: Path, record: dict[str, object], cases: Sequence[Case],
                          measured: dict[str, list[dict[str, object]]], seed: str,
                          headroom: Mapping[str, object]) -> dict[str, object]:
    """Write the report. `headroom` is the headroom block: it gives the saturated families and the seed signals.

    `calls` and `retries` are the spent counts. `approved_calls` and `estimate` are the approved ceiling and the plan.
    """
    scores = {name: [unit_score(item["score"]) for item in items] for name, items in measured.items()}
    estimate = mapping(field(record, "estimate"))
    report: dict[str, object] = NOT_RUN | {
        "sharing": "private-local-only",
        "exposed": record["exposed"],
        "cases": {case.identifier: {"split": case.split, "family": case.family, "scores": scores[case.identifier],
                                   "unmet": [unmet_criteria(item) for item in measured[case.identifier]]}
                  for case in cases},
        "family_means": _family_means(cases, scores),
        "saturated_families": _saturated(headroom),
        "warnings": headroom["warnings"],
        "unscorable": headroom["unscorable"],
        "calls": int_field(record, "calls"),
        "retries": int_field(record, "retries"),
        "approved_calls": int_field(mapping(field(record, "budget")), "maximum"),
        "estimate": {key: estimate[key] for key in ("calls", "seconds")},
        "calls_by_role": record.get("calls_by_role"),
        "seconds_by_role": record.get("seconds_by_role"),
        "usage_by_role": usage_by_role(record),
    } | _diagnostics(measured, seed)
    write(out / REPORT_NAME, report)
    return report
