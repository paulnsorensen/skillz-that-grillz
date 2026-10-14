from __future__ import annotations

import importlib
import threading
from collections.abc import Mapping, Sequence
from typing import cast

import pytest

from skillz_experiments._budget import MAX_CONCURRENT_CALLS
from skillz_experiments.search import Edit, search


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
                  metric_calls=80) is False
    assert proposed[:4] == ["bad", "mid", "top", "late"]
    assert "bad" not in validated
    assert set(validated) >= {"v0", "mid", "top", "late"}


@pytest.mark.ac("skillz-pragmatic-autoimprove:AC-7")
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

    assert search({"SKILL.md": "v0"}, "prose", ["t1"], ["val1"], evaluate, propose, metric_calls=4) is False
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

    assert search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], evaluate, propose, metric_calls=12) is False
    assert proposed == []
    assert evaluated and set(evaluated) == {"v0"}


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

    assert search(seed, edit, ["t1", "t2"], ["v"], evaluate, propose, metric_calls=30) is False
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
                  metric_calls=80, stalled=lambda: len(proposed) >= after) is True
    assert len(proposed) == after


def test_search_rejects_an_empty_seed() -> None:
    with pytest.raises(ValueError, match="no editable components"):
        _ = search({}, "prose", ["t"], ["v"], lambda c, e: (0.0, {}), lambda c, f, k: {}, metric_calls=5)


def test_search_rejects_an_unknown_edit() -> None:
    with pytest.raises(ValueError, match="prose or prose\\+cli"):
        _ = search({"SKILL.md": "x"}, cast(Edit, cast(object, "cli")), ["t"], ["v"], lambda c, e: (0.0, {}),
                   lambda c, f, k: {}, metric_calls=5)
