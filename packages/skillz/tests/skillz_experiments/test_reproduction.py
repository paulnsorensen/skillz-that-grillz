"""gh-651 and gh-663: the regression field and the reproduction record of one case.

The tests that drive a full run are in `test_reproduction_records.py`, `test_reproduction_holdout.py`, and
`test_reproduction_floor.py`, so that each file stays under the test-time budget.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from skillz_experiments._cases import Case, CodedError
from skillz_experiments._evals_format import parse_journeys
from skillz_experiments._intake import DRAFT_NAME, case_hash, load_draft, split_cases
from skillz_experiments.run._plan import VALIDATION_REPEATS, repeat_indices
from skillz_experiments.run._reproduce import floor_cases, reproductions


def _case(**extra: object) -> dict[str, object]:
    return {"id": "field-1", "family": "f", "kind": "task", "request": "Echo the file.", "files": {"fixture.md": "hi\n"},
            "expected": {"criteria": [{"id": "answer", "text": "THE answer SHALL echo the file."}]},
            "source": "skill"} | extra


def _draft(tmp_path: Path, *cases: dict[str, object]) -> Path:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(list(cases)), encoding="utf-8")
    return path


@pytest.mark.ac("gh-651:AC-1")
def test_a_session_case_accepts_regression_true_in_the_draft_and_the_journeys_file(tmp_path: Path) -> None:
    cases = load_draft(_draft(tmp_path, _case(source="session", regression=True)))
    assert [case.regression for case in cases] == [True]
    journey = {"id": "j1", "kind": "journey", "request": "Start.", "brief": "You want x.", "expected": {"must": ["x"]},
               "source": "session", "regression": True}
    parsed = parse_journeys([journey], strict=False)
    assert parsed.errors == () and parsed.cases[0]["regression"] is True


@pytest.mark.ac("gh-651:AC-1")
@pytest.mark.parametrize(("regression", "source", "reason"), [
    ("yes", "session", "true or false"), (1, "session", "true or false"), (None, "session", "true or false"),
    (True, "skill", "needs source: session")])
def test_a_bad_regression_value_stops_as_cases_invalid_naming_the_case(
        tmp_path: Path, regression: object, source: str, reason: str) -> None:
    with pytest.raises(CodedError) as draft:
        _ = load_draft(_draft(tmp_path, _case(source=source, regression=regression)))
    assert draft.value.code == "cases-invalid" and "field-1" in str(draft.value) and reason in str(draft.value)
    journey = {"id": "j1", "kind": "journey", "request": "Start.", "brief": "You want x.", "expected": {"must": ["x"]},
               "source": source, "regression": regression}
    parsed = parse_journeys([journey], strict=False)
    assert parsed.cases == () and len(parsed.errors) == 1 and "'j1'" in parsed.errors[0] and reason in parsed.errors[0]


@pytest.mark.ac("gh-651:AC-1")
@pytest.mark.parametrize("kind", ["trigger", "near-miss"])
def test_a_pending_draft_case_cannot_take_regression_true(tmp_path: Path, kind: str) -> None:
    with pytest.raises(CodedError) as draft:
        _ = load_draft(_draft(tmp_path, _case(kind=kind, source="session", regression=True)))
    assert draft.value.code == "cases-invalid" and "field-1" in str(draft.value) and "scored kind" in str(draft.value)
    assert [case.regression for case in load_draft(_draft(tmp_path, _case(kind=kind, source="session")))] == [False]


@pytest.mark.ac("gh-651:AC-2")
def test_an_omitted_or_false_regression_gives_the_same_case_data_and_hash(tmp_path: Path) -> None:
    hashes: list[str] = []
    for extra in ({}, {"regression": False}):
        cases = [_case(id=f"c{index}", family=f"f{index}", **extra) for index in range(6)]
        drafted = split_cases(load_draft(_draft(tmp_path, *cases)), 7)
        assert all("regression" not in case.data() for case in drafted)
        hashes.append(case_hash(drafted, 7))
    assert hashes[0] == hashes[1]
    cases = [_case(id=f"c{index}", family=f"f{index}", **({"source": "session", "regression": True} if index == 0 else {}))
             for index in range(6)]
    marked = split_cases(load_draft(_draft(tmp_path, *cases)), 7)
    assert [case.data().get("regression") for case in marked] == [True, None, None, None, None, None]


@pytest.mark.ac("gh-651:AC-3")
def test_a_regression_case_with_no_scored_outcome_records_null_with_a_reason() -> None:
    case = Case("r", "f", "train", "request", {"a": "b"}, {}, "session", True, regression=True)
    plain = Case("p", "f", "train", "request", {"a": "b"}, {}, "skill", True)
    found = reproductions([case, plain], {}, {})
    assert found == {"r": {"split": "train", "solvable": "unknown", "scores": [], "reproduces": None,
                           "reason": "no scored seed outcome"}}
    assert floor_cases(found) == set()


@pytest.mark.ac("gh-651:AC-3")
@pytest.mark.ac("gh-663:AC-1")
def test_a_seed_score_just_above_zero_is_partial_and_unmet_keeps_the_lowest_level() -> None:
    case = Case("v", "f", "validation", "request", {"a": "b"}, {}, "session", True, regression=True)

    def outcome(score: float, level: int) -> dict[str, object]:
        return {"score": score, "criteria": {"gap": {"level": level, "reason": f"level {level}", "met": False}}}

    scores, levels = [5e-13] + [0.0] * (VALIDATION_REPEATS - 1), [1] + [2] * (VALIDATION_REPEATS - 1)
    items: dict[tuple[object, object, object], dict[str, object]] = {
        ("validation", "v", repeat): outcome(score, level)
        for repeat, score, level in zip(repeat_indices("validation"), scores, levels, strict=True)}
    found = reproductions([case], items, {})
    assert (found["v"]["reproduces"], found["v"]["state"]) == (False, "partial") and floor_cases(found) == set()
    assert found["v"]["unmet"] == {"gap": {"level": 1, "reason": "level 1"}}


@pytest.mark.ac("gh-663:AC-1")
@pytest.mark.ac("gh-663:AC-2")
@pytest.mark.parametrize(("first", "rest", "reproduces", "state", "floor"), [
    (0.0, 0.0, True, "fail-to-pass", False),
    (0.5, 0.5, False, "partial", False),
    (1.0, 0.0, False, "partial", False),
    (0.99, 1.0, False, "partial", False),
    (1.0, 1.0, False, "pass-to-pass", True),
])
def test_the_reproduction_state_follows_the_seed_scores_and_only_pass_to_pass_is_a_floor_case(
        first: float, rest: float, reproduces: bool, state: str, floor: bool) -> None:
    scores = [first] + [rest] * (VALIDATION_REPEATS - 1)
    case = Case("v", "f", "validation", "request", {"a": "b"}, {}, "session", True, regression=True)
    items: dict[tuple[object, object, object], dict[str, object]] = {
        ("validation", "v", repeat): {"score": score} for repeat, score in zip(repeat_indices("validation"), scores, strict=True)}
    found = reproductions([case], items, {})
    assert (found["v"]["reproduces"], found["v"]["state"]) == (reproduces, state)
    assert floor_cases(found) == ({"v"} if floor else set())


@pytest.mark.ac("gh-663:AC-1")
@pytest.mark.ac("gh-663:AC-2")
@pytest.mark.parametrize(("score", "state", "floor"), [
    (0.0, "fail-to-pass", False), (0.5, "partial", False), (1.0, "pass-to-pass", True)])
def test_a_single_repeat_train_case_and_a_holdout_case_take_a_state_from_one_score(
        score: float, state: str, floor: bool) -> None:
    outcome: dict[str, object] = {"score": score}
    train = Case("t", "f", "train", "request", {"a": "b"}, {}, "session", True, regression=True)
    held = Case("h", "g", "holdout", "request", {"a": "b"}, {}, "session", True, regression=True)
    found = reproductions([train, held], {("train", "t", None): outcome}, {"h": outcome})
    for name in ("t", "h"):
        assert (found[name]["state"], found[name]["reproduces"]) == (state, score == 0.0)
    assert floor_cases(found) == ({"t", "h"} if floor else set())


@pytest.mark.ac("gh-663:AC-1")
@pytest.mark.ac("gh-663:AC-2")
def test_a_record_with_no_scored_outcome_carries_no_state_and_a_stale_state_never_makes_a_floor_case() -> None:
    holdout = Case("h", "f", "holdout", "request", {"a": "b"}, {}, "session", True, regression=True)
    found = reproductions([holdout], {}, {})
    assert "state" not in found["h"] and found["h"]["reproduces"] is None
    assert floor_cases({"x": {"reproduces": False}, "y": {"state": "fail-to-pass"}, "z": {"state": "partial"}}) == set()


@pytest.mark.ac("gh-663:AC-2")
def test_floor_cases_picks_only_the_pass_to_pass_cases_from_a_mixed_set() -> None:
    cases = [Case(name, "f", "train", "request", {"a": "b"}, {}, "session", True, regression=True) for name in "abc"]
    items: dict[tuple[object, object, object], dict[str, object]] = {
        ("train", "a", None): {"score": 1.0}, ("train", "b", None): {"score": 0.5}, ("train", "c", None): {"score": 0.0}}
    assert floor_cases(reproductions(cases, items, {})) == {"a"}


@pytest.mark.ac("gh-651:AC-9")
def test_a_holdout_regression_case_keeps_only_the_level_of_each_unmet_criterion() -> None:
    outcome: dict[str, object] = {"score": 0.0, "criteria": {"gap": {"level": 2, "reason": "the draft quotes the holdout text", "met": False},
                                          "ok": {"level": 4, "reason": "fine", "met": True}}}
    held = Case("h", "f", "holdout", "request", {"a": "b"}, {}, "session", True, regression=True)
    trained = Case("t", "g", "train", "request", {"a": "b"}, {}, "session", True, regression=True)
    found = reproductions([held, trained], {("train", "t", None): outcome}, {"h": outcome})
    assert found["h"]["unmet"] == {"gap": {"level": 2}} and found["h"]["reproduces"] is True
    assert found["t"]["unmet"] == {"gap": {"level": 2, "reason": "the draft quotes the holdout text"}}
