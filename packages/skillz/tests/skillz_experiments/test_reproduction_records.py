"""gh-651: a full run records the seed scores of each train and validation regression case."""
# pyright: reportAny=false
from __future__ import annotations

from pathlib import Path

import pytest

from skillz_experiments.run._plan import VALIDATION_REPEATS

from _reproduction_runs import Approver, Drafter, Maker, go, reproduction, seed_zero, splits, start

pytestmark = pytest.mark.usefixtures("host_login")


@pytest.mark.ac("gh-651:AC-3")
def test_a_train_or_validation_regression_case_records_its_seed_scores_and_reproduces_when_all_are_zero(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_zero(state)
    _ = go(state, target, out, approved, calls)
    found = reproduction(out)
    scored = splits(out, "train", "validation")
    assert scored and {name for name, item in found.items() if item["split"] != "holdout"} == set(scored)
    for name, split in scored.items():
        item = found[name]
        assert item["split"] == split and item["solvable"] == "unknown" and item["reproduces"] is True
        assert item["scores"] == [0.0] * (len(item["scores"])) and len(item["scores"]) == (VALIDATION_REPEATS if split == "validation" else 1)
        assert isinstance(item["unmet"], dict)


@pytest.mark.ac("gh-651:AC-3")
def test_a_regression_case_that_the_seed_passes_on_one_repeat_does_not_reproduce(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    passed: list[int] = []

    def partial(text: str, split: str) -> float | None:
        """Score the first validation seed call 1.0 and every other seed call 0."""
        if "improved" in text:
            return None
        if split == "validation" and not passed:
            passed.append(1)
            return 1.0
        return 0.0

    state.score_of = partial
    _ = go(state, target, out, approved, calls)
    found = reproduction(out)
    validation = splits(out, "validation")
    floor = [name for name in validation if found[name]["reproduces"] is False]
    assert len(floor) == 1 and 1.0 in found[floor[0]]["scores"] and 0.0 in found[floor[0]]["scores"]
    assert all(item["reproduces"] is True for name, item in found.items() if name != floor[0])
