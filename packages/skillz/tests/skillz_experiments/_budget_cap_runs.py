"""Shared run steps of the budget-cap tests: the case receipt, a started run, and a refused search approval (gh-588)."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._intake import load_draft, split_cases
from skillz_experiments.run import Stop, run
from test_run_search import MODEL, Search

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]


def receipt(target: Path, out: Path) -> Stop:
    """Return the `cases-unapproved` stop of a first call."""
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL)
    assert cases.value.code == "cases-unapproved"
    return cases.value


def start(tmp_path: Path, make_target: Maker, write_draft: Drafter, *, large: bool = False) -> tuple[Path, Path, str]:
    """Draft the cases and return the target, the run directory, and the case hash.

    `large` adds 40 cases to one holdout family, so a search at three repeats does not fit the standard cap.
    """
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    stop = receipt(target, out)
    if large:
        draft = out / "cases.draft.json"
        split = split_cases(load_draft(draft), cast(int, stop.data["seed"]))
        family = next(case.family for case in split if case.split == "holdout")
        rows = cast(list[dict[str, object]], json.loads(draft.read_text()))
        example = next(row for row in rows if row["family"] == family)
        rows.extend(example | {"id": f"large-{i}", "request": f"request-large-{i}-end"} for i in range(40))
        _ = draft.write_text(json.dumps(rows))
        stop = receipt(target, out)
    return target, out, cast(str, stop.data["case_hash"])


def refused(target: Path, out: Path, case_hash: str, repeats: int | None = None,
            approve_budget: int | None = None) -> Stop:
    """Measure the baseline once, then return the `budget-unapproved` stop of a search request."""
    if not (out / "run.json").exists():
        with pytest.raises(Stop) as baseline_budget:
            _ = run(target, out, MODEL, approve_cases=case_hash, repeats=repeats)
        assert baseline_budget.value.code == "budget-unapproved"
        calls = cast(dict[str, int], baseline_budget.value.data["estimate"])["calls"]
        baseline = run(target, out, MODEL, live=True, approve_cases=case_hash, repeats=repeats,
                       approve_budget=calls, factory=Search(target).factory)
        assert baseline["search"] == "not-run"
    with pytest.raises(Stop) as stop:
        _ = run(target, out, MODEL, search=True, approve_budget=approve_budget)
    assert stop.value.code == "budget-unapproved"
    return stop.value
