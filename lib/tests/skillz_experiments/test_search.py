from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import cast

import pytest

from skillz_experiments._runtime import MAX_CONCURRENT_CALLS
from skillz_experiments._search import Edit, search


def test_search_runs_several_pareto_iterations_and_picks_by_validation_mean() -> None:
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

    search({"SKILL.md": "v0"}, "prose", ["t1", "t2", "t3"], ["val1", "val2"], evaluate, propose,
           metric_calls=80)
    assert proposed[:4] == ["bad", "mid", "top", "late"]
    assert "bad" not in validated
    assert set(validated) >= {"v0", "mid", "top", "late"}
    best = max(set(validated), key=lambda text: validation_scores.get(text, 0.0))
    assert best == "top"


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

    search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], evaluate, propose, metric_calls=12)
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

    assert search(seed, edit, ["t1", "t2"], ["v"], evaluate, propose, metric_calls=30) is None
    assert offered == set(seed)
    assert evaluated and all(names == set(seed) for names in evaluated)
    assert MAX_CONCURRENT_CALLS == 2


def test_search_rejects_an_empty_seed() -> None:
    with pytest.raises(ValueError, match="no editable components"):
        search({}, "prose", ["t"], ["v"], lambda c, e: (0.0, {}), lambda c, f, k: {}, metric_calls=5)


def test_search_rejects_an_unknown_edit() -> None:
    with pytest.raises(ValueError, match="prose or prose\\+cli"):
        _ = search({"SKILL.md": "x"}, cast(Edit, cast(object, "cli")), ["t"], ["v"], lambda c, e: (0.0, {}),
                   lambda c, f, k: {}, metric_calls=5)
