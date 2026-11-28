"""The match rule of an outcome row and the four search-phase lookups that use it."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._cases import Case, digest
from skillz_experiments.run._match import matches
from test_token_feedback import CHILD, SEED_ROWS, _evaluate, _train, _used
from test_validation_repeats import _row
from test_validation_repeats_press import FakeState, _candidate, _phase

ROW: dict[str, object] = {"search_candidate": "c", "split": "validation", "case_id": "v0", "repeat": 1}
BARE: dict[str, object] = {"search_candidate": "c", "split": "validation", "case_id": "v0"}


@pytest.mark.parametrize(("item", "filters", "expected"), [
    (ROW, {}, True),
    (ROW, {"split": "validation"}, True),
    (ROW, {"split": "train"}, False),
    (ROW, {"repeat": 1}, True),
    (ROW, {"repeat": 0}, False),
    (ROW, {"repeat": None}, False),
    (BARE, {"repeat": None}, True),
    (BARE, {"repeat": 0}, False),
    (ROW | {"status": "evaluation-failed"}, {"split": "validation", "repeat": 1}, True),
])
def test_matches_applies_only_the_filters_that_the_caller_gives(
        item: dict[str, object], filters: dict[str, object], expected: bool) -> None:
    assert matches(item, candidate="c", case_id="v0", **filters) is expected


@pytest.mark.parametrize(("candidate", "case_id"), [("other", "v0"), ("c", "v1")])
def test_matches_needs_the_candidate_and_the_case(candidate: str, case_id: str) -> None:
    assert not matches(ROW, candidate=candidate, case_id=case_id)


def test_a_missing_candidate_matches_only_a_row_without_one() -> None:
    assert matches({"case_id": "v0"}, candidate=None, case_id="v0")
    assert not matches(ROW, candidate=None, case_id="v0")


def test_the_reuse_index_holds_no_failed_row_and_no_row_without_a_candidate() -> None:
    child = _candidate("child")
    outcomes: list[dict[str, object]] = [_row(child.identity, "v0", 0.5, 0), _row(child.identity, "v0", 0.0, 1, failed=True),
                                         {"arm": "search", "split": "reflection", "case_id": "v0"}]
    phase = _phase(FakeState(outcomes, {}, cases=["v0"]), child)
    assert phase.recorded_for_reuse() == 1
    assert list(phase._reusable) == [(child.identity, "validation", "v0", 0)]


def _case(split: str = "validation") -> Case:
    return cast(Case, cast(object, SimpleNamespace(identifier="v0", split=split)))


def test_a_reuse_lookup_without_a_repeat_takes_only_a_scored_row_without_a_repeat() -> None:
    child = _candidate("child")
    with_repeat = _row(child.identity, "v0", 0.5, 0)
    failed = _row(child.identity, "v0", 0.0, None, failed=True)
    phase = _phase(FakeState([with_repeat, failed], {}, cases=["v0"]), child)
    assert phase._reuse(child, _case(), None) is None
    bare = _row(child.identity, "v0", 0.25, None)
    phase = _phase(FakeState([bare, with_repeat, failed], {}, cases=["v0"]), child)
    assert phase._reuse(child, _case(), None) is bare
    assert phase._reuse(child, _case("train"), None) is None


def test_the_incumbent_token_mean_ignores_the_split_and_drops_a_failed_row() -> None:
    other_split = _used(_row("seed", "t0", 0.5, None), 400)
    failed = _row("seed", "t0", 0.0, None, failed=True) | {"split": "train"}
    _, feedback = _evaluate([*SEED_ROWS, other_split, _train("seed", 800), failed, _train(CHILD, 300)], "t0")
    assert (feedback["task_tokens"], feedback["incumbent_task_tokens"]) == (300.0, 600.0)


def test_the_train_tokens_come_from_the_newest_scored_train_row_of_any_repeat() -> None:
    child = _candidate("child")
    outcomes = [_train("seed", 1000), _train("seed", 500), _train(CHILD, 300) | {"repeat": 1},
                _row(CHILD, "t0", 0.0, None, failed=True) | {"split": "train"}, _used(_row(CHILD, "t0", 0.5, None), 900)]
    phase = _phase(FakeState(outcomes, {}, cases=["v0"]), child)
    assert (phase._train_tokens("seed", ["t0"]), phase._train_tokens(CHILD, ["t0"])) == (500.0, 300.0)
    assert phase._train_tokens(CHILD, ["t1"]) is None
    phase._identities.update({digest({"SKILL.md": "seed"}): "seed", digest({"SKILL.md": "child"}): CHILD})
    assert phase.cheaper({"SKILL.md": "seed"}, {"SKILL.md": "child"}, ["t0"])
    assert not phase.cheaper({"SKILL.md": "child"}, {"SKILL.md": "seed"}, ["t0"])
