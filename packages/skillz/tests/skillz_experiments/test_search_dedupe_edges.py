"""Edges of the concurrent-request dedupe of the search `score` (gh-757)."""
from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from typing import cast

import pytest

from skillz_experiments.search import search

Score = tuple[float, dict[str, object]]
Evaluator = Callable[[dict[str, str], object], Score]


def _drive(monkeypatch: pytest.MonkeyPatch, drive: Callable[[Evaluator, dict[str, str]], None],
           evaluate: Evaluator) -> None:
    api = importlib.import_module("gepa.optimize_anything")

    def fake(seed: dict[str, str], **kwargs: object) -> None:
        drive(cast(Evaluator, kwargs["evaluator"]), seed)

    monkeypatch.setattr(api, "optimize_anything", fake)
    _ = search({"SKILL.md": "v0"}, "prose", ["t1", "t2"], ["val1"], evaluate,
               lambda _c, _f, components: {key: "x" for key in components}, metric_calls=4)


@pytest.mark.ac("gh-757:AC-8")
def test_a_failing_pair_raises_in_both_concurrent_requests_and_a_later_request_runs_again(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    started = threading.Event()
    outcomes: list[str] = []

    def evaluate(_candidate: dict[str, str], example: object) -> Score:
        calls.append(example)
        if len(calls) == 1:
            started.set()
            _ = threading.Event().wait(0.2)
            raise RuntimeError("provider down")
        return 0.5, {}

    def attempt(evaluator: Evaluator, seed: dict[str, str]) -> None:
        try:
            _ = evaluator(seed, "t1")
        except RuntimeError as error:
            outcomes.append(str(error))

    def drive(evaluator: Evaluator, seed: dict[str, str]) -> None:
        first = threading.Thread(target=attempt, args=(evaluator, seed))
        first.start()
        assert started.wait(5)
        attempt(evaluator, seed)
        first.join()
        assert calls == ["t1"] and outcomes == ["provider down"] * 2
        # The failed pair leaves no pending entry: the next request runs the evaluator again.
        assert evaluator(seed, "t1") == (0.5, {})

    _drive(monkeypatch, drive, evaluate)
    assert calls == ["t1", "t1"]


@pytest.mark.ac("gh-757:AC-8")
def test_concurrent_requests_for_another_candidate_or_another_case_are_not_merged(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    lock = threading.Lock()
    gate = threading.Barrier(3)

    def evaluate(candidate: dict[str, str], example: object) -> Score:
        with lock:
            calls.append((candidate["SKILL.md"], example))
        _ = gate.wait(5)
        return 0.5, {}

    def drive(evaluator: Evaluator, seed: dict[str, str]) -> None:
        other = {"SKILL.md": "v1"}
        pairs: list[tuple[dict[str, str], str]] = [(seed, "t1"), (other, "t1"), (seed, "t2")]
        threads = [threading.Thread(target=evaluator, args=pair) for pair in pairs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    _drive(monkeypatch, drive, evaluate)
    assert sorted(calls) == [("v0", "t1"), ("v0", "t2"), ("v1", "t1")]
