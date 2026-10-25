from __future__ import annotations

import importlib
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast, final

import pytest

from skillz_experiments._budget import MAX_CONCURRENT_CALLS
from skillz_experiments._cases import CodedError
from skillz_experiments.search import Edit, search
from skillz_experiments.search._engine import CheaperTie, overshoot, skip_limit


@pytest.mark.ac("skillz-pragmatic-autoimprove:AC-7")
def test_search_runs_several_pareto_iterations_and_validates_only_gated_candidates() -> None:
    train_scores = {"v0": 0.2, "bad": 0.0, "mid": 0.5, "top": 0.6, "late": 0.9}
    validation_scores = {"v0": 0.2, "mid": 0.7, "top": 0.8, "late": 0.3}
    proposals = iter(["bad", "mid", "top", "late"])
    lock = threading.Lock()
    validated: list[str] = []
    proposed: list[str] = []

    def evaluate(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        text = candidate["SKILL.md"]
        if str(example).startswith("val"):
            with lock:
                validated.append(text)
            return validation_scores.get(text, 0.0), {"task_correct": 0.0}
        return train_scores.get(text, 0.0), {"task_correct": 0.0}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        text = next(proposals, "extra")
        proposed.append(text)
        return {key: text for key in components}

    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2", "t3"], ["val1", "val2"], evaluate, propose,
                  metric_calls=80) != "stall"
    assert proposed[:4] == ["bad", "mid", "top", "late"]
    assert "bad" not in validated
    assert set(validated) >= {"v0", "mid", "top", "late"}


@pytest.mark.ac("skillz-pragmatic-autoimprove:AC-7")
@pytest.mark.ac("gh-500:AC-6")
def test_search_passes_pareto_parent_selection_to_gepa(monkeypatch: pytest.MonkeyPatch) -> None:
    api = importlib.import_module("gepa.optimize_anything")
    seen: list[object] = []

    def capture(*_args: object, **kwargs: object) -> None:
        seen.append(kwargs["config"])

    monkeypatch.setattr(api, "optimize_anything", capture)
    def evaluate(_candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        return 0.0, {}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        return {key: "x" for key in components}

    assert search({"SKILL.md": "v0"}, "prose", ["t1"], ["val1"], evaluate, propose, metric_calls=4) != "stall"
    assert len(seen) == 1
    engine = cast(object, getattr(seen[0], "engine"))
    assert cast(object, getattr(engine, "candidate_selection_strategy")) == "pareto"


@pytest.mark.ac("skillz-pragmatic-autoimprove:AC-7")
def test_search_skips_a_minibatch_the_parent_already_scores_perfectly() -> None:
    proposed: list[str] = []

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        proposed.append("called")
        return {key: "changed" for key in components}

    evaluated: list[str] = []

    def evaluate(candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        evaluated.append(candidate["SKILL.md"])
        return 1.0, {}

    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], evaluate, propose, metric_calls=12) != "stall"
    assert proposed == []
    assert evaluated and set(evaluated) == {"v0"}


def _tied(answer: bool) -> tuple[list[str], list[tuple[str, str, list[object]]]]:
    """Run a token-rule search where every candidate scores 1.0. Return the validated texts and the `cheaper` calls."""
    validated: list[str] = []
    asked: list[tuple[str, str, list[object]]] = []
    lock = threading.Lock()

    def evaluate(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        if str(example).startswith("val"):
            with lock:
                validated.append(candidate["SKILL.md"])
        return 1.0, {"task_correct": 1.0}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        return {key: "cheap" for key in components}

    def cheaper(parent: dict[str, str], child: dict[str, str], examples: Sequence[object]) -> bool:
        with lock:
            asked.append((parent["SKILL.md"], child["SKILL.md"], list(examples)))
        return answer

    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], evaluate, propose, metric_calls=8,
                  cheaper=cheaper) != "stall"
    return validated, asked


@pytest.mark.ac("skillz-search-token-tie:AC-1")
def test_a_token_rule_search_reflects_on_a_perfect_parent_and_validates_a_cheaper_tie() -> None:
    validated, asked = _tied(True)
    assert asked and asked[0][:2] == ("v0", "cheap")
    assert all(set(examples) <= {"t1", "t2"} and examples for _, _, examples in asked)
    assert "cheap" in validated


@pytest.mark.ac("skillz-search-token-tie:AC-1")
def test_a_token_rule_search_keeps_an_equal_child_that_is_not_cheaper_out_of_the_pool() -> None:
    validated, asked = _tied(False)
    assert asked
    assert set(validated) == {"v0"}


@pytest.mark.ac("skillz-search-token-tie:AC-2")
def test_without_the_token_rule_the_search_passes_gepa_the_strict_rule_and_the_perfect_skip(
        monkeypatch: pytest.MonkeyPatch) -> None:
    api = importlib.import_module("gepa.optimize_anything")
    seen: list[object] = []

    def capture(*_args: object, **kwargs: object) -> None:
        seen.append(kwargs["config"])

    monkeypatch.setattr(api, "optimize_anything", capture)

    def evaluate(_candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        return 0.0, {}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        return {key: "x" for key in components}

    def always(_parent: dict[str, str], _child: dict[str, str], _examples: Sequence[object]) -> bool:
        return True

    for cheaper in (None, always):
        assert search({"SKILL.md": "v0"}, "prose", ["t1"], ["val1"], evaluate, propose, metric_calls=4,
                      cheaper=cheaper) != "stall"
    plain, token = (cast(object, getattr(config, "engine")) for config in seen)
    reflections = [cast(object, getattr(config, "reflection")) for config in seen]
    assert cast(object, getattr(plain, "acceptance_criterion")) == "strict_improvement"
    assert cast(object, getattr(token, "acceptance_criterion")) != "strict_improvement"
    assert [cast(object, getattr(item, "skip_perfect_score")) for item in reflections] == [True, False]


@final
class _Recorded:
    """Score each candidate and case once, as the session does: a repeat of a scored pair makes no live call.

    The first `rejected` proposals fail admission, as a leak or a failed build does: they score 0.0 with no live call.
    """

    def __init__(self, train: dict[str, float], other: float, rejected: int = 0) -> None:
        self.train, self.other = train, other
        self.rejected = {f"p{index}" for index in range(1, rejected + 1)}
        self.lock = threading.Lock()
        self.scored: dict[tuple[str, str], float] = {}
        self.repeats = 0
        self.proposals = 0
        self.checks = 0

    def evaluate(self, candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
        key = (candidate["SKILL.md"], str(example))
        if key[0] in self.rejected:
            return 0.0, {"task_correct": 0.0, "rejected": "leakage"}
        with self.lock:
            if key in self.scored:
                self.repeats += 1
            elif key[0] == "v0":
                self.scored[key] = self.train.get(key[1], self.other)
            else:
                self.scored[key] = self.other
            return self.scored[key], {"task_correct": self.scored[key]}

    def propose(self, _candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        with self.lock:
            self.proposals += 1
            return {key: f"p{self.proposals}" for key in components}

    def stalled(self) -> bool:
        with self.lock:
            self.checks += 1
        return False


@pytest.mark.ac("skillz-search-free-repeats:AC-1")
def test_free_repeats_of_a_mostly_perfect_parent_do_not_end_the_search_before_its_share() -> None:
    """Kit-like: 3 train and 3 validation cases, 43 metric calls. The seed scores 1.0 on two train cases.

    GEPA counts each repeat of the parent and each skipped minibatch. Before the fix, those free repeats ended the
    search after 7 proposals with 19 scored pairs. Each rejected proposal scores 2 new pairs.
    """
    run = _Recorded({"t1": 1.0, "t2": 1.0, "t3": 0.8}, 0.7)
    validation: list[object] = ["val1", "val2", "val3"]
    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2", "t3"], validation, run.evaluate, run.propose,
                  metric_calls=43, stalled=run.stalled) != "stall"
    assert run.repeats > 0
    assert 43 <= len(run.scored) <= 43 + overshoot(len(validation))
    assert run.proposals >= 18


@pytest.mark.ac("skillz-search-free-repeats:AC-2")
@pytest.mark.ac("gh-500:AC-7")
def test_a_search_with_only_perfect_minibatches_ends_after_the_skip_limit() -> None:
    run = _Recorded({"t1": 1.0, "t2": 1.0, "t3": 1.0}, 1.0)
    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2", "t3"], ["val1", "val2", "val3"], run.evaluate,
                  run.propose, metric_calls=10**6, stalled=run.stalled) == "idle"
    assert run.proposals == 0
    # One check before the first iteration, 2 iterations that score the train cases, then the idle limit.
    assert run.checks == 1 + 2 + skip_limit(3) == 7
    assert len(run.scored) == 6


@pytest.mark.ac("skillz-search-free-repeats:AC-4")
def test_rejected_proposals_do_not_use_the_search_share() -> None:
    """Five rejected proposals add 10 pairs with no live call. Before the fix, they ended the search at 12 pairs."""
    run = _Recorded({"t1": 1.0, "t2": 1.0, "t3": 0.8}, 0.7, rejected=5)
    validation: list[object] = ["val1", "val2", "val3"]
    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2", "t3"], validation, run.evaluate, run.propose,
                  metric_calls=12, stalled=run.stalled) != "stall"
    assert run.proposals > 5
    assert 12 <= len(run.scored) <= 12 + overshoot(len(validation))

@pytest.mark.parametrize("edit", ["prose", "prose+cli"])
def test_search_offers_and_evaluates_exactly_the_seed_components(edit: Edit) -> None:
    seed = {"SKILL.md": "seed", "references/a.md": "ref"}
    offered: set[str] = set()
    evaluated: list[set[str]] = []

    def evaluate(candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        evaluated.append(set(candidate))
        return float(candidate["SKILL.md"] == "changed"), {"task_correct": 0.0}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        offered.update(components)
        return {key: "changed" for key in components}

    assert search(seed, edit, ["t1", "t2"], ["v"], evaluate, propose, metric_calls=30) != "stall"
    assert offered == set(seed)
    assert evaluated and all(names == set(seed) for names in evaluated)
    assert MAX_CONCURRENT_CALLS == 2


@pytest.mark.ac("gh-240:AC-4")
@pytest.mark.parametrize("after", [0, 2])
def test_search_stops_before_the_next_iteration_when_stalled_returns_true(after: int) -> None:
    proposed: list[str] = []

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        proposed.append("called")
        return {key: f"p{len(proposed)}" for key in components}

    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], lambda _c, _e: (0.0, {}), propose,
                  metric_calls=80, stalled=lambda: len(proposed) >= after) == "stall"
    assert len(proposed) == after


def test_search_rejects_an_empty_seed() -> None:
    with pytest.raises(ValueError, match="no editable components"):
        _ = search({}, "prose", ["t"], ["v"], lambda c, e: (0.0, {}), lambda c, f, k: {}, metric_calls=5)


def test_search_rejects_an_unknown_edit() -> None:
    with pytest.raises(ValueError, match="prose or prose\\+cli"):
        _ = search({"SKILL.md": "x"}, cast(Edit, cast(object, "cli")), ["t"], ["v"], lambda c, e: (0.0, {}),
                   lambda c, f, k: {}, metric_calls=5)


@dataclass
class _Proposal:
    """The GEPA proposal fields that the token tie reads."""
    subsample_scores_before: list[float] | None
    subsample_scores_after: list[float] | None
    parent_program_ids: list[int] = field(default_factory=lambda: [0])
    candidate: dict[str, str] = field(default_factory=lambda: {"SKILL.md": "child"})
    subsample_indices: list[int] | None = field(default_factory=lambda: [1, 0])


@dataclass
class _State:
    program_candidates: list[dict[str, str]]


@pytest.mark.ac("skillz-search-token-tie:AC-1")
def test_the_token_tie_accepts_a_gain_rejects_a_loss_and_asks_cheaper_only_on_a_tie() -> None:
    asked: list[tuple[str, str, list[object]]] = []

    def cheaper(parent: dict[str, str], child: dict[str, str], examples: Sequence[object]) -> bool:
        asked.append((parent["SKILL.md"], child["SKILL.md"], list(examples)))
        return True

    tie = CheaperTie(cheaper, ["t1", "t2"])
    state = _State([{"SKILL.md": "seed"}])
    assert tie.should_accept(_Proposal([0.5, 0.5], [1.0, 0.5]), state) is True
    assert tie.should_accept(_Proposal([1.0, 1.0], [1.0, 0.9]), state) is False
    assert asked == []
    assert tie.should_accept(_Proposal([0.1, 0.2], [0.2, 0.1]), state) is True
    assert asked == [("seed", "child", ["t2", "t1"])]
    assert tie.should_accept(_Proposal([1.0, 1.0], [1.0, 1.0], [0, 0]), state) is False


@pytest.mark.ac("gh-401:AC-1")
def test_search_stops_with_a_coded_fault_on_an_uncoded_proposer_exception() -> None:
    def evaluate(_candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        return 0.0, {"task_correct": 0.0}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                _components: list[str]) -> dict[str, str]:
        raise RuntimeError("boom")

    with pytest.raises(CodedError, match="boom") as raised:
        _ = search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], evaluate, propose, metric_calls=20)
    assert raised.value.code == "proposer-faulted"


@pytest.mark.ac("gh-401:AC-2")
@pytest.mark.parametrize("error", [ValueError("skip"), CodedError("some-code", "coded")])
def test_search_keeps_a_proposer_value_error_as_a_skipped_proposal(error: ValueError) -> None:
    def evaluate(_candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        return 0.0, {"task_correct": 0.0}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                _components: list[str]) -> dict[str, str]:
        raise error

    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], evaluate, propose, metric_calls=20) != "stall"
