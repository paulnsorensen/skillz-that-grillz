"""The leaderboard names the incumbent by the identity that its search outcomes record (gh-410)."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments.gate import DEFAULT_STATISTICS
from skillz_experiments.run._leaderboard import Leaderboard
from skillz_experiments.run._state import RunState
from skillz_experiments.search import protected

VALIDATION = ("v1", "v2")
TRAIN = ("t1", "t2")


def _outcomes(identity: str, split: str, cases: tuple[str, ...], score: float) -> list[dict[str, object]]:
    """Return one outcome per train case, and one per repeat of each validation case."""
    repeats: tuple[int | None, ...] = (0, 1) if split == "validation" else (None,)
    rows: list[dict[str, object]] = []
    for case_id in cases:
        for repeat in repeats:
            row: dict[str, object] = {"arm": "search", "split": split, "search_candidate": identity, "case_id": case_id,
                                      "score": score}
            if repeat is not None:
                row["repeat"] = repeat
            rows.append(row)
    return rows


def _cases_for(_split: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(identifier=item, family="f") for item in VALIDATION]


def _state(seed: Candidate, built: Candidate, score: float) -> RunState:
    """Return a run state where the seed scores 0.5 and `built` scores `score` on each validation repeat.

    `built` has full train scores. The Leaderboard has compared `built` with the seed.
    """
    outcomes = (_outcomes(seed.identity, "validation", VALIDATION, 0.5) + _outcomes(built.identity, "validation", VALIDATION, score)
                + _outcomes(built.identity, "train", TRAIN, 1.0))
    state = SimpleNamespace(lock=threading.RLock(), statistics=DEFAULT_STATISTICS, outcomes=outcomes,
                            record={}, seed=seed, checkpoint=lambda: None, cases_for=_cases_for)
    board = cast(RunState, cast(object, state))
    Leaderboard(board).keep_if_best(built, cast(Case, cast(object, SimpleNamespace(split="validation", identifier="v2"))))
    return board


def _built() -> tuple[Candidate, Candidate]:
    seed = Candidate({"SKILL.md": "x", "@wedge/a/main.py": "VERSION = 1\n"}, ("@wedge/a/main.py",),
                     frozen={"scripts/a.pyz": b"1"})
    return seed, seed.changed({"@wedge/a/main.py": "VERSION = 2\n"}).with_frozen({"scripts/a.pyz": b"2"})


@pytest.mark.ac("gh-410:AC-1")
def test_best_identity_is_the_recorded_identity_of_a_winner_whose_own_target_was_rebuilt() -> None:
    seed, built = _built()
    state = _state(seed, built, 1.0)
    assert seed.changed({"@wedge/a/main.py": "VERSION = 2\n"}).identity != built.identity
    leaderboard = Leaderboard(state)
    assert leaderboard.best_identity() == built.identity
    assert protected(state.outcomes, leaderboard.best_identity()) == list(TRAIN)
    assert leaderboard.best_validated().files == built.files


@pytest.mark.ac("gh-410:AC-1")
def test_best_identity_is_the_seed_when_the_challenger_does_not_beat_the_seed() -> None:
    seed, built = _built()
    state = _state(seed, built, 0.25)
    assert Leaderboard(state).best_identity() == seed.identity
    assert Leaderboard(state).best_validated().files == seed.files
