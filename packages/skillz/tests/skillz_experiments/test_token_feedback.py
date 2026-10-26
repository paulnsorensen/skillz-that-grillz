"""The proposer feedback carries task tokens when the run sets a token rule (gh-537)."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

from types import SimpleNamespace

import pytest

from skillz_experiments.gate import Statistics
from test_validation_repeats import _arm, _row
from test_validation_repeats_press import FakeState, _candidate, _phase

TOKEN_RULE = Statistics(min_token_saving=0.1, max_quality_loss=0.05)
CHILD = _candidate("child").identity
SEED_ROWS = _arm("seed", [1.0, 1.0], tokens=[1000], cases=["v0"])
"""The seed outcomes on `v0`: 1000 tokens and a higher score, so the seed stays the incumbent."""


def _used(row: dict[str, object], tokens: int | None) -> dict[str, object]:
    """Return `row` with `tokens` uncached task tokens, or `row` unchanged when `tokens` is None."""
    return row if tokens is None else row | {"usage": {"input_tokens": tokens, "cached_input_tokens": 0, "output_tokens": 0}}


def _child(tokens: int | None) -> list[dict[str, object]]:
    """Return the two scored repeats of the child on `v0`, each with `tokens` uncached task tokens when given."""
    return [_used(_row(CHILD, "v0", 0.5, repeat), tokens) for repeat in (0, 1)]


def _train(identity: str, tokens: int) -> dict[str, object]:
    """Return one scored outcome of `identity` on the train case `t0` with `tokens` uncached task tokens."""
    return _used(_row(identity, "t0", 0.5, None) | {"split": "train"}, tokens)


def _evaluate(outcomes: list[dict[str, object]], example: str, *, statistics: Statistics = TOKEN_RULE,
              failing: tuple[tuple[str, int], ...] = ()) -> tuple[FakeState, dict[str, object]]:
    """Return the state over `outcomes` and the feedback of the child on `example`.

    `t0` is a train case. A repeat that the record lacks runs live, and each repeat in `failing` fails at the provider.
    """
    state = FakeState(outcomes, {("v0", 0): 0.5, ("v0", 1): 0.5}, cases=["v0"])
    state.statistics = statistics
    state.failing.update(failing)
    phase = _phase(state, _candidate("child"))
    vars(phase)["_search_cases"]["t0"] = SimpleNamespace(identifier="t0", family="f9", split="train", request="t0")
    _ = phase.recorded_for_reuse()
    _, feedback = phase.evaluate_example({"SKILL.md": "child"}, example)
    return state, feedback


def _feedback(statistics: Statistics, child_tokens: int | None) -> dict[str, object]:
    """Return the feedback of the child on `v0`. The seed, with 1000 tokens and a higher score, stays the incumbent."""
    state, feedback = _evaluate([*SEED_ROWS, *_child(child_tokens)], "v0", statistics=statistics)
    assert state.live == [], "every repeat comes from the record"
    return feedback


@pytest.mark.ac("gh-537:AC-1")
def test_a_token_rule_adds_the_case_tokens_and_the_incumbent_mean_to_the_feedback() -> None:
    feedback = _feedback(TOKEN_RULE, 600)
    assert (feedback["task_tokens"], feedback["incumbent_task_tokens"]) == (600.0, 1000.0)


@pytest.mark.ac("gh-537:AC-1")
def test_a_train_case_gets_its_own_tokens_and_the_incumbent_mean_on_that_case() -> None:
    state, feedback = _evaluate([*SEED_ROWS, _train("seed", 800), _train(CHILD, 300)], "t0")
    assert state.live == [], "the train run comes from the record"
    assert (feedback["case_id"], feedback["task_tokens"], feedback["incumbent_task_tokens"]) == ("t0", 300.0, 800.0)


@pytest.mark.ac("gh-537:AC-1")
def test_an_incumbent_without_outcomes_on_the_case_leaves_its_field_out() -> None:
    _, feedback = _evaluate([*SEED_ROWS, _train(CHILD, 300)], "t0")
    assert feedback["task_tokens"] == 300.0 and "incumbent_task_tokens" not in feedback


@pytest.mark.ac("gh-537:AC-1")
def test_a_failed_repeat_stays_out_of_the_task_token_mean() -> None:
    state, feedback = _evaluate([*SEED_ROWS, _used(_row(CHILD, "v0", 0.5, 0), 600)], "v0", failing=(("v0", 1),))
    assert state.live == [("v0", 1)] and state.outcomes[-1]["status"] == "evaluation-failed"
    assert (feedback["task_tokens"], feedback["incumbent_task_tokens"]) == (600.0, 1000.0)


@pytest.mark.ac("gh-537:AC-1")
def test_an_unscored_case_gets_no_token_fields() -> None:
    _, feedback = _evaluate(list(SEED_ROWS), "v0", failing=(("v0", 0), ("v0", 1)))
    assert feedback == {"unscored": True, "request": "v0"}


@pytest.mark.ac("gh-537:AC-2")
def test_a_run_without_a_token_rule_keeps_the_feedback_unchanged() -> None:
    assert _feedback(Statistics(), 600) == {"task_correct": pytest.approx(0.5), "request": "v0", "case_id": "v0"}


@pytest.mark.ac("gh-537:AC-1")
def test_an_unknown_count_leaves_its_field_out() -> None:
    feedback = _feedback(TOKEN_RULE, None)
    assert "task_tokens" not in feedback and feedback["incumbent_task_tokens"] == 1000.0
