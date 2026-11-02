"""The stall observation counts a validation-equal, cheaper proposal as progress only in a token-rule run."""
from __future__ import annotations

import math

import pytest

from skillz_experiments.run._outcomes import stall_observations
from skillz_experiments.search import assess

VALIDATION = {"v1", "v2"}


def _row(candidate: str, case: str, score: float, tokens: int | None) -> dict[str, object]:
    row: dict[str, object] = {"arm": "search", "split": "validation", "case_id": case, "search_candidate": candidate,
                              "score": score}
    if tokens is not None:
        row["usage"] = {"input_tokens": tokens, "cached_input_tokens": 0, "output_tokens": 0}
    return row


def _proposal(score: float, tokens: int | None, *, seed_tokens: int | None = 100) -> list[dict[str, object]]:
    reflection: dict[str, object] = {"arm": "search", "split": "reflection", "proposed": "child"}
    return [*(_row("seed", case, 0.5, seed_tokens) for case in sorted(VALIDATION)), reflection,
            *(_row("child", case, score, tokens) for case in sorted(VALIDATION))]


@pytest.mark.ac("gh-529:AC-3")
def test_an_equal_and_cheaper_proposal_resets_the_stall_streak_in_a_token_rule_run() -> None:
    observations = stall_observations(_proposal(0.5, 60), VALIDATION, cheaper=True)
    assert observations == [math.inf]
    stalled = [0.0] * 5
    assert assess([*stalled, *observations], band=0.0).streak == 0
    assert assess([*stalled, 0.0], band=0.0).streak == 6


@pytest.mark.ac("gh-529:AC-3")
@pytest.mark.parametrize("score, tokens, seed_tokens", [
    (0.5, 100, 100),    # equal tokens
    (0.5, 160, 100),    # more tokens
    (0.5, None, 100),   # unknown candidate tokens
    (0.5, 60, None),    # unknown baseline tokens
    (0.4, 60, 100),     # lower quality
])
def test_a_tie_that_is_not_known_to_be_cheaper_stays_a_stall_in_a_token_rule_run(
        score: float, tokens: int | None, seed_tokens: int | None) -> None:
    assert stall_observations(_proposal(score, tokens, seed_tokens=seed_tokens), VALIDATION, cheaper=True) == [0.0]


@pytest.mark.ac("gh-529:AC-3")
def test_without_a_token_field_the_observations_do_not_change() -> None:
    outcomes = _proposal(0.5, 60)
    assert stall_observations(outcomes, VALIDATION) == [0.0]
    assert stall_observations(outcomes, VALIDATION, cheaper=False) == [0.0]
    assert stall_observations(_proposal(0.75, 60), VALIDATION, cheaper=True) == [pytest.approx(0.25)]
