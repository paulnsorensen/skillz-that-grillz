"""Adversarial tests of repeated validation runs and the 2-SE incumbent rule (gh-500 press)."""
# pyright: reportPrivateUsage=false, reportAny=false, reportExplicitAny=false
from __future__ import annotations

import math
import random
import threading
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any, cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments.gate import DEFAULT_STATISTICS
from skillz_experiments.run._leaderboard import Leaderboard
from skillz_experiments.run._outcomes import stall_observations
from skillz_experiments.run._search_phase import SearchPhase
from skillz_experiments.run._state import RunState
from skillz_experiments.search import (
    add_validation_scores, is_reflection, is_search_validation, search, unit_score, validation_complete,
)
from test_validation_repeats import CASES, SEED, _arm, _board, _row

Outcomes = list[dict[str, object]]
Feedback = Mapping[str, Sequence[Mapping[str, object]]]


def _candidate(text: str) -> Candidate:
    return Candidate(files={"SKILL.md": text}, editable=("SKILL.md",))


@final
class FakeState:
    """The run state that SearchPhase and Leaderboard read, with a scripted provider."""

    def __init__(self, outcomes: Outcomes, scores: dict[tuple[str, int], float], cases: Sequence[str] = CASES) -> None:
        self.lock: threading.RLock = threading.RLock()
        self.statistics = DEFAULT_STATISTICS
        self.outcomes: Outcomes = outcomes
        self.record: dict[str, object] = {}
        self.seed = SEED
        self.scores = scores
        self.failing: set[tuple[str, int]] = set()
        self.live: list[tuple[str, int | None]] = []
        self.found = [SimpleNamespace(identifier=name, family=f"f{index // 2}", split="validation", request=name)
                      for index, name in enumerate(cases)]

    def checkpoint(self) -> None:
        return None

    def cases_for(self, split: str) -> list[Any]:
        return self.found if split == "validation" else []

    def evaluate_case(self, candidate: Candidate, case: Any, arm: str, *, repeat: int | None = None) -> dict[str, object]:
        assert arm == "search" and repeat is not None
        with self.lock:
            self.live.append((case.identifier, repeat))
        if (case.identifier, repeat) in self.failing:
            raise RuntimeError("provider fails: down")
        row = _row(candidate.identity, case.identifier, self.scores[(case.identifier, repeat)], repeat)
        with self.lock:
            self.outcomes.append(row)
        return row


def _phase(state: FakeState, candidate: Candidate) -> SearchPhase:
    """Return a SearchPhase over `state` that admits every proposal as `candidate`."""
    def admit(_components: dict[str, str]) -> Candidate:
        return candidate

    phase = object.__new__(SearchPhase)
    vars(phase).update(_state=cast(RunState, cast(object, state)),
                       _search_cases={case.identifier: case for case in state.found},
                       _reusable={}, _identities={}, _live=0, _admit=admit)
    return phase


def _offer(state: RunState, challenger: Candidate | SimpleNamespace) -> list[dict[str, object]]:
    Leaderboard(state).keep_if_best(cast(Candidate, cast(object, challenger)),
                                    cast(Case, cast(object, SimpleNamespace(split="validation", identifier="v0"))))
    search_record = cast(dict[str, object], state.record.get("search", {}))
    return cast(list[dict[str, object]], search_record.get("comparisons", []))


def _named(identity: str) -> SimpleNamespace:
    return SimpleNamespace(identity=identity, editable=("SKILL.md",), files={"SKILL.md": identity})


@pytest.mark.ac("gh-500:AC-1")
def test_evaluate_example_resumed_with_only_repeat_zero_runs_exactly_repeat_one_live() -> None:
    child = _candidate("child")
    outcomes = [*_arm("seed", [1.0, 1.0], cases=["v0"]), _row(child.identity, "v0", 0.5, 0)]
    state = FakeState(outcomes, {("v0", 1): 1.0}, cases=["v0"])
    phase = _phase(state, child)
    assert phase.recorded_for_reuse() == 3
    score, feedback = phase.evaluate_example({"SKILL.md": "child"}, "v0")
    assert state.live == [("v0", 1)]
    fresh = [item for item in outcomes if item.get("search_candidate") == child.identity]
    assert [(item["repeat"], item["score"]) for item in fresh] == [(0, 0.5), (1, 1.0)]
    assert score == pytest.approx(0.75) and feedback["task_correct"] == pytest.approx(0.75)
    # GEPA asks for the same example again as the next parent: both repeats now come from the record.
    again, _ = phase.evaluate_example({"SKILL.md": "child"}, "v0")
    assert state.live == [("v0", 1)] and again == pytest.approx(0.75)


@pytest.mark.ac("gh-500:AC-1")
def test_evaluate_example_resumed_with_no_repeat_key_row_runs_both_repeats_live() -> None:
    """A pre-change row has no `repeat`; it must not serve a repeat of the new policy."""
    child = _candidate("child")
    outcomes = [*_arm("seed", [1.0, 1.0], cases=["v0"]), _row(child.identity, "v0", 0.5, None)]
    state = FakeState(outcomes, {("v0", 0): 1.0, ("v0", 1): 1.0}, cases=["v0"])
    phase = _phase(state, child)
    _ = phase.recorded_for_reuse()
    _ = phase.evaluate_example({"SKILL.md": "child"}, "v0")
    assert state.live == [("v0", 0), ("v0", 1)]


@pytest.mark.ac("gh-500:AC-11")
def test_evaluate_example_retry_after_failed_repeat_runs_only_that_repeat_and_feeds_gepa_the_newest_mean() -> None:
    child = _candidate("child")
    outcomes = _arm("seed", [1.0, 1.0], cases=["v0"])
    state = FakeState(outcomes, {("v0", 0): 0.5, ("v0", 1): 1.0}, cases=["v0"])
    state.failing = {("v0", 1)}
    phase = _phase(state, child)
    _ = phase.recorded_for_reuse()
    first, feedback = phase.evaluate_example({"SKILL.md": "child"}, "v0")
    assert first == pytest.approx(0.5) and feedback["task_correct"] == pytest.approx(0.5)
    assert validation_complete(outcomes, child.identity, {"v0"}, 2)
    state.failing.clear()
    state.live.clear()
    second, _ = phase.evaluate_example({"SKILL.md": "child"}, "v0")
    # The failed repeat is not reused; the scored repeat 0 is.
    assert state.live == [("v0", 1)]
    assert second == pytest.approx(0.75)
    assert add_validation_scores({}, outcomes)[child.identity] == {"v0": pytest.approx(0.75)}


@pytest.mark.ac("gh-500:AC-11")
@pytest.mark.parametrize("failed_last", [False, True])
def test_leaderboard_uses_the_newest_scored_outcome_of_a_repeat_whatever_failed_around_it(failed_last: bool) -> None:
    rows = _arm("c", [1.0, 1.0], cases=CASES[:3])
    rows += [_row("c", "v3", 1.0, 0)]
    retry = [_row("c", "v3", 0.0, 1, failed=True), _row("c", "v3", 1.0, 1)]
    rows += retry[::-1] if failed_last else retry
    state = _board([*_arm("seed", [0.5, 0.5]), *rows])
    (comparison,) = _offer(state, _named("c"))
    assert comparison["delta"] == pytest.approx(0.5)
    assert comparison["verdict"] == "promote"
    assert add_validation_scores({}, rows)["c"]["v3"] == 1.0


@pytest.mark.ac("gh-500:AC-3")
def test_keep_if_best_compares_a_later_challenger_with_the_new_incumbent_and_each_pair_once() -> None:
    outcomes = [*_arm("seed", [0.5, 0.25]), *_arm("c1", [1.0, 0.75]), *_arm("c2", [0.5, 0.25]), *_arm("c3", [1.0, 0.75])]
    state = _board(outcomes)
    assert [(c["challenger"], c["incumbent"]) for c in _offer(state, _named("c1"))] == [("c1", "seed")]
    assert Leaderboard(state).best_identity() == "c1"
    _ = _offer(state, _named("c1"))
    _ = _offer(state, _named("c2"))
    _ = _offer(state, _named("c2"))
    found = _offer(state, _named("c3"))
    assert [(c["challenger"], c["incumbent"]) for c in found] == [("c1", "seed"), ("c2", "c1"), ("c3", "c1")]
    assert [c["verdict"] for c in found][1:] == ["reject", "inconclusive"] or found[1]["verdict"] != "promote"
    assert Leaderboard(state).best_identity() == "c1"
    # The seed is never a challenger, and the incumbent is never compared with itself.
    assert len(_offer(state, SEED)) == 3


@pytest.mark.ac("gh-500:AC-11")
def test_a_challenger_with_every_repeat_failed_on_one_case_drops_that_case_and_the_verdict_still_runs() -> None:
    rows = [*_arm("c", [1.0, 0.75], cases=CASES[:3]), _row("c", "v3", 0.0, 0, failed=True), _row("c", "v3", 0.0, 1, failed=True)]
    state = _board([*_arm("seed", [0.5, 0.25]), *rows])
    (comparison,) = _offer(state, _named("c"))
    assert comparison["verdict"] == "promote" and comparison["delta"] == pytest.approx(0.5)
    kept = cast(dict[str, object], state.record["best_validated"])
    assert kept["identity"] == "c"


@pytest.mark.ac("gh-500:AC-5")
def test_the_engine_debits_nothing_for_a_rejected_validation_result_even_with_two_repeats() -> None:
    def run(rejected: bool) -> int:
        proposals: list[str] = []

        def evaluate(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
            feedback: dict[str, object] = {"task_correct": 0.0}
            if rejected and example == "v1":
                feedback["rejected"] = "build failed"
            return (0.5 if candidate["SKILL.md"] == "seed" else 0.0), feedback

        def propose(_candidate: dict[str, str], _feedback: Feedback, components: list[str]) -> dict[str, str]:
            proposals.append("called")
            return {name: f"child {len(proposals)}" for name in components}

        _ = search({"SKILL.md": "seed"}, "prose", ["t1", "t2"], ["v1"], evaluate, propose, metric_calls=10,
                   validation_repeats=2)
        return len(proposals)

    # Debited: 2 (v1) + 2 (seed train) + 2 per child reach 10 after 3 proposals. A rejected v1 debits 0: 4 proposals.
    assert run(rejected=False) == 3
    assert run(rejected=True) == 4


def _old_stall(outcomes: Sequence[Mapping[str, object]], validation: set[str]) -> list[float | None]:
    """The stall algorithm before the change (56baea1c), for outcomes without `repeat`."""
    def add(scores: dict[str, dict[str, float]], items: Sequence[Mapping[str, object]]) -> None:
        for item in items:
            if is_search_validation(item) and item.get("status") != "evaluation-failed":
                scores.setdefault(cast(str, item["search_candidate"]), {})[cast(str, item["case_id"])] = unit_score(item["score"])

    def means(scores: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
        return {key: math.fsum(by.values()) / len(by) for key, by in scores.items() if set(by) == validation}

    marks = [index for index, item in enumerate(outcomes) if is_reflection(item)]
    seen: dict[str, dict[str, float]] = {}
    add(seen, outcomes[:marks[0]] if marks else [])
    found: list[float | None] = []
    for start, end in zip(marks, [*marks[1:], len(outcomes)]):
        best = max(means(seen).values(), default=0.0)
        window = outcomes[start + 1:end]
        proposed = outcomes[start].get("proposed")
        scores = dict(seen.get(proposed, {})) if isinstance(proposed, str) else {}
        result: float | None = None
        if outcomes[start].get("status") is None:
            result = -1.0
            for item in window:
                if proposed is None or set(scores) == validation:
                    break
                if item.get("search_candidate") != proposed:
                    continue
                if item.get("status") == "evaluation-failed":
                    result = None
                    break
                if item.get("split") == "validation":
                    scores[cast(str, item["case_id"])] = unit_score(item["score"])
            if result is not None:
                result = 0.0 if set(scores) != validation else max(0.0, math.fsum(scores.values()) / len(scores) - best)
        found.append(result)
        add(seen, window)
    return found


def _random_outcomes(rng: random.Random) -> Outcomes:
    names = ["s", "c1", "c2"]
    rows: Outcomes = []
    for _ in range(rng.randint(0, 40)):
        kind = rng.random()
        if kind < 0.2:
            row: dict[str, object] = {"arm": "search", "split": "reflection"}
            if rng.random() < 0.8:
                row["proposed"] = rng.choice(names)
            if rng.random() < 0.15:
                row["status"] = rng.choice(["skipped", "timeout", "proposal-build-failed"])
            rows.append(row)
        else:
            split = rng.choice(["validation", "validation", "train"])
            rows.append(_row(rng.choice(names), rng.choice(["a", "b"]), rng.choice([0.0, 0.25, 0.5, 1.0]), None,
                             failed=rng.random() < 0.15) | {"split": split})
    return rows


@pytest.mark.ac("gh-500:AC-9")
def test_stall_observations_with_one_repeat_match_the_old_algorithm_on_outcomes_without_repeat_keys() -> None:
    rng = random.Random(500)
    for _ in range(600):
        rows = _random_outcomes(rng)
        assert stall_observations(rows, {"a", "b"}, 1) == _old_stall(rows, {"a", "b"}), rows
