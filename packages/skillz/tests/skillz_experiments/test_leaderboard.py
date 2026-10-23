"""The leaderboard names the best candidate by the identity that its search outcomes record (gh-410)."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
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
    return [SimpleNamespace(identifier=item) for item in VALIDATION]


def _state(seed: Candidate, built: Candidate) -> RunState:
    """Return a run state whose kept best candidate is `built`, with full train scores and a validation win."""
    outcomes = (_outcomes(seed.identity, "validation", VALIDATION, 0.5) + _outcomes(built.identity, "validation", VALIDATION, 1.0)
                + _outcomes(built.identity, "train", TRAIN, 1.0))
    kept = {"identity": built.identity, "mean": 1.0, "files": {name: built.files[name] for name in seed.editable}}
    state = SimpleNamespace(lock=threading.RLock(), statistics=DEFAULT_STATISTICS, outcomes=outcomes,
                            record={"best_validated": kept}, seed=seed, checkpoint=lambda: None,
                            cases_for=_cases_for)
    return cast(RunState, cast(object, state))


@pytest.mark.ac("gh-410:AC-1")
def test_best_identity_is_the_recorded_identity_of_a_winner_whose_own_target_was_rebuilt() -> None:
    seed = Candidate({"SKILL.md": "x", "@wedge/a/main.py": "VERSION = 1\n"}, ("@wedge/a/main.py",),
                     frozen={"scripts/a.pyz": b"1"})
    built = seed.changed({"@wedge/a/main.py": "VERSION = 2\n"}).with_frozen({"scripts/a.pyz": b"2"})
    state = _state(seed, built)
    assert seed.changed({"@wedge/a/main.py": "VERSION = 2\n"}).identity != built.identity
    leaderboard = Leaderboard(state)
    assert leaderboard.best_identity() == built.identity
    assert protected(state.outcomes, leaderboard.best_identity()) == list(TRAIN)
    assert leaderboard.best_validated(list(VALIDATION)).files == built.files


@pytest.mark.ac("gh-410:AC-1")
def test_best_identity_is_the_seed_when_the_kept_candidate_does_not_beat_the_seed() -> None:
    seed = Candidate({"SKILL.md": "x", "@wedge/a/main.py": "VERSION = 1\n"}, ("@wedge/a/main.py",),
                     frozen={"scripts/a.pyz": b"1"})
    built = seed.changed({"@wedge/a/main.py": "VERSION = 2\n"}).with_frozen({"scripts/a.pyz": b"2"})
    state = _state(seed, built)
    state.outcomes[:] = [item for item in state.outcomes if item["search_candidate"] == seed.identity]
    state.outcomes.extend(_outcomes(built.identity, "validation", VALIDATION, 0.25))
    assert Leaderboard(state).best_identity() == seed.identity
