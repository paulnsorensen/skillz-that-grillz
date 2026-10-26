"""The proposer feedback carries task tokens when the run sets a token rule (gh-537)."""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import pytest

from skillz_experiments.gate import Statistics
from test_validation_repeats import _arm, _row
from test_validation_repeats_press import FakeState, _candidate, _phase

TOKEN_RULE = Statistics(min_token_saving=0.1, max_quality_loss=0.05)


def _child(tokens: int | None) -> list[dict[str, object]]:
    """Return the two scored repeats of the child on `v0`, each with `tokens` uncached task tokens when given."""
    rows = [_row(_candidate("child").identity, "v0", 0.5, repeat) for repeat in (0, 1)]
    usage = {} if tokens is None else {"usage": {"input_tokens": tokens, "cached_input_tokens": 0, "output_tokens": 0}}
    return [row | usage for row in rows]


def _feedback(statistics: Statistics, child_tokens: int | None) -> dict[str, object]:
    """Return the feedback of the child on `v0`. The seed, with 1000 tokens and a higher score, stays the incumbent."""
    state = FakeState([*_arm("seed", [1.0, 1.0], tokens=[1000], cases=["v0"]), *_child(child_tokens)], {}, cases=["v0"])
    state.statistics = statistics
    phase = _phase(state, _candidate("child"))
    _ = phase.recorded_for_reuse()
    _, feedback = phase.evaluate_example({"SKILL.md": "child"}, "v0")
    assert state.live == [], "every repeat comes from the record"
    return feedback


@pytest.mark.ac("gh-537:AC-1")
def test_a_token_rule_adds_the_case_tokens_and_the_incumbent_mean_to_the_feedback() -> None:
    feedback = _feedback(TOKEN_RULE, 600)
    assert (feedback["task_tokens"], feedback["incumbent_task_tokens"]) == (600.0, 1000.0)


@pytest.mark.ac("gh-537:AC-2")
def test_a_run_without_a_token_rule_keeps_the_feedback_unchanged() -> None:
    assert _feedback(Statistics(), 600) == {"task_correct": pytest.approx(0.5), "request": "v0", "case_id": "v0"}


@pytest.mark.ac("gh-537:AC-1")
def test_an_unknown_count_leaves_its_field_out() -> None:
    feedback = _feedback(TOKEN_RULE, None)
    assert "task_tokens" not in feedback and feedback["incumbent_task_tokens"] == 1000.0
