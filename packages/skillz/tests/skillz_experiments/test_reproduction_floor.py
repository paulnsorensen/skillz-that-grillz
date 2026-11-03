"""gh-651 and gh-663: only a pass-to-pass regression case leaves its family mean in a full run."""
# pyright: reportAny=false
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments.run import Stop

from _reproduction_runs import Approver, Drafter, Maker, go, record, seed_one_on_train, splits, start

pytestmark = pytest.mark.usefixtures("host_login")


@pytest.mark.ac("gh-663:AC-3")
def test_a_family_with_a_pass_to_pass_and_a_partial_regression_case_averages_only_the_partial_one(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)

    def score(text: str, split: str, case_id: str) -> float | None:
        """Score the seed 1.0 on each train case that ends in -0, 0.5 on each other train case, and 0 elsewhere."""
        if "improved" in text:
            return None
        return (1.0 if case_id.endswith("-0") else 0.5) if split == "train" else 0.0

    state.case_score_of = score
    _ = go(state, target, out, approved, calls)
    headroom = record(out)["headroom"]
    train = splits(out, "train")
    assert {headroom["reproduction"][name]["state"] for name in train} == {"pass-to-pass", "partial"}
    families = [item for item in headroom["families"].values() if item["split"] == "train"]
    assert families and all(item["cases"] == 1 and item["mean"] == pytest.approx(0.5) for item in families)
    assert headroom["verdict"] == "ok"


@pytest.mark.ac("gh-663:AC-3")
def test_a_partially_failing_regression_case_stays_in_its_family_mean_so_the_run_is_not_too_easy(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(
        tmp_path, make_target, write_draft, approvals, regression=lambda case: str(case["id"]).endswith("-0"))

    def score(text: str, split: str, case_id: str) -> float | None:
        """Score the seed 0.5 on each train regression case, 0.95 on each other train case, and 0 elsewhere."""
        if "improved" in text:
            return None
        if split != "train":
            return 0.0
        return 0.5 if case_id.endswith("-0") else 0.95

    state.case_score_of = score
    _ = go(state, target, out, approved, calls)
    headroom = record(out)["headroom"]
    partial = {name for name in splits(out, "train") if name.endswith("-0")}
    assert partial and all(headroom["reproduction"][name]["state"] == "partial" for name in partial)
    assert headroom["verdict"] == "ok"
    train = [item for item in headroom["families"].values() if item["split"] == "train"]
    assert train and all(item["cases"] == 2 and item["mean"] == pytest.approx(0.725) for item in train)


@pytest.mark.ac("gh-651:AC-5")
def test_a_family_with_only_floor_cases_does_not_stop_the_run_and_the_floor_stays_listed(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(tmp_path, make_target, write_draft, approvals)
    seed_one_on_train(state)
    _ = go(state, target, out, approved, calls)
    headroom = record(out)["headroom"]
    train = splits(out, "train")
    assert headroom["verdict"] == "ok"
    assert all(headroom["reproduction"][name]["reproduces"] is False for name in train)
    assert not {item["split"] for item in headroom["families"].values()} & {"train"}


@pytest.mark.ac("gh-651:AC-5")
@pytest.mark.ac("gh-651:AC-7")
def test_a_floor_case_stays_out_of_its_family_mean_and_a_too_easy_stop_carries_the_reproduction(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = start(
        tmp_path, make_target, write_draft, approvals, regression=lambda case: str(case["id"]).endswith("-0"))
    seed_one_on_train(state)
    with pytest.raises(Stop) as caught:
        _ = go(state, target, out, approved, calls)
    assert caught.value.code == "cases-too-easy"
    headroom = record(out)["headroom"]
    train = splits(out, "train")
    floors = {name for name in train if name.endswith("-0")}
    assert floors and {name for name, item in headroom["reproduction"].items() if item["split"] == "train"} == floors
    # Each train family has one floor case and one other case: only the other case adds to the mean.
    assert {item["cases"] for item in headroom["families"].values() if item["split"] == "train"} == {1}
    baseline = cast(dict[str, Any], caught.value.data["baseline"])  # pyright: ignore[reportExplicitAny]
    assert baseline["headroom"]["reproduction"] == headroom["reproduction"]
