"""Attempts: rebuilt from recorded outcomes alone, joined to the later train and validation scores."""
from __future__ import annotations

import pytest

from skillz_experiments.search import attempts, protected

SEED = ("digest-seed", "seed-id")


def _score(candidate: str, split: str, case: str, score: float, **extra: object) -> dict[str, object]:
    return {"arm": "search", "split": split, "case_id": case, "search_candidate": candidate, "score": score} | extra


def _reflection(**fields: object) -> dict[str, object]:
    return {"arm": "search", "split": "reflection", "parent": "digest-seed", "components_digest": "digest-1",
            "minibatch": ["a", "b", "c"], "hypothesis": "say it shorter", "hypothesis_dropped": None,
            "rejected": None, "proposed": "child-1"} | fields


@pytest.mark.ac("gh-241:AC-1")
def test_an_evaluated_proposal_records_fixed_and_broke_and_a_duplicate_resolves_to_the_newest_scored_outcome() -> None:
    outcomes = [
        _score("seed-id", "train", "a", 0.5), _score("seed-id", "train", "b", 1.0), _score("seed-id", "train", "c", 0.5),
        _score("seed-id", "validation", "v", 0.4),
        _reflection(),
        _score("child-1", "train", "a", 0.2),
        _score("child-1", "train", "a", 0.9),
        _score("child-1", "train", "a", 0.0, status="evaluation-failed"),
        _score("child-1", "train", "b", 0.0), _score("child-1", "train", "c", 0.5),
        _score("child-1", "validation", "v", 0.6),
    ]
    (attempt,) = attempts(outcomes, SEED)
    assert attempt["fixed"] == ["a"] and attempt["broke"] == ["b"]
    assert attempt == {"index": 1, "parent": "seed-id", "candidate": "child-1", "components": "digest-1",
                       "hypothesis": "say it shorter", "fixed": ["a"], "broke": ["b"],
                       "validation_delta": pytest.approx(0.2), "accepted": True, "rejected": None}


@pytest.mark.ac("gh-241:AC-1")
def test_an_unscored_child_has_empty_lists_and_a_null_validation_delta() -> None:
    outcomes = [_score("seed-id", "train", "a", 0.5), _score("seed-id", "validation", "v", 0.4), _reflection(),
                _score("child-1", "train", "a", 0.0, status="evaluation-failed")]
    (attempt,) = attempts(outcomes, SEED)
    assert attempt["fixed"] == [] and attempt["broke"] == [] and attempt["validation_delta"] is None


@pytest.mark.ac("gh-241:AC-1")
def test_the_parent_of_a_later_proposal_resolves_through_the_earlier_proposal() -> None:
    outcomes = [_score("seed-id", "train", "a", 0.1), _reflection(),
                _reflection(parent="digest-1", components_digest="digest-2", proposed="child-2", minibatch=["a"]),
                _score("child-1", "train", "a", 0.5), _score("child-2", "train", "a", 0.25)]
    second = attempts(outcomes, SEED)[1]
    assert second["index"] == 2 and second["parent"] == "child-1"
    assert second["broke"] == ["a"] and second["fixed"] == []


@pytest.mark.ac("gh-241:AC-9")
def test_a_legacy_outcome_and_an_unresolved_parent_give_empty_lists_and_a_rejected_attempt_has_no_candidate() -> None:
    legacy = {"arm": "search", "split": "reflection", "proposed": "child-1"}
    outcomes = [_score("seed-id", "train", "a", 0.1), _score("child-1", "train", "a", 0.9), legacy,
                _reflection(parent="unknown"),
                _reflection(proposed=None, rejected="candidate-leakage-rejected", hypothesis=None)]
    first, second, third = attempts(outcomes, SEED)
    for attempt in (first, second):
        assert attempt["fixed"] == [] and attempt["broke"] == [] and attempt["validation_delta"] is None
    assert first["parent"] is None and first["components"] is None
    assert third["candidate"] is None and third["accepted"] is False and third["rejected"] == "candidate-leakage-rejected"
    assert [attempt["index"] for attempt in (first, second, third)] == [1, 2, 3]


def test_the_protect_set_holds_the_train_cases_with_a_full_newest_score() -> None:
    outcomes = [_score("best", "train", "a", 1.0), _score("best", "train", "b", 1.0), _score("best", "train", "b", 0.5),
                _score("best", "train", "c", 1.0), _score("best", "train", "c", 0.0, status="evaluation-failed"),
                _score("best", "validation", "v", 1.0), _score("other", "train", "d", 1.0)]
    assert protected(outcomes, "best") == ["a", "c"]
