"""Finalist resume regressions with real search accounting and leaderboard rules."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._records import read
from skillz_experiments.gate import Statistics
from skillz_experiments.run import run
from skillz_experiments.run._fault import FaultLatch
from skillz_experiments.run._finalists import Finalists
from skillz_experiments.run._leaderboard import Leaderboard
from skillz_experiments.run._search_phase import SearchPhase
from skillz_experiments.run._state import RunState
from test_run_search import MODEL, Search, _start  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("gh-245:AC-3")
def test_resume_keeps_completed_comparisons_in_proposal_order(tmp_path: Path) -> None:
    seed = Candidate({"SKILL.md": "seed"}, ("SKILL.md",))
    candidates = [seed.changed({"SKILL.md": name}) for name in ("A", "B", "C")]
    cases = [Case(str(index), "guarded" if index < 2 else "other", "validation", "request",
                  {}, {}, "fixture", True) for index in range(12)]
    record: dict[str, object] = {"split_seed": 0, "budget": {"reserve_seconds": 0},
                                "estimate": {"metric_calls": 200, "headroom": {"outcomes": 0}}}
    outcomes: list[dict[str, object]] = []
    snapshots: list[tuple[dict[str, object], list[dict[str, object]]]] = []

    def checkpoint() -> None:
        snapshots.append((deepcopy(record), deepcopy(outcomes)))

    def evaluate(candidate: Candidate, case: Case, _arm: str, *, repeat: int = 0) -> dict[str, object]:
        if candidate == candidates[2] and interrupt:
            raise CodedError("other-code", "interrupt C")
        guarded = int(case.identifier) < 2
        score = (0.5 if candidate == seed else (0.35 if guarded else 0.9) if candidate == candidates[0]
                 else (0.425 if guarded else 0.75))
        row: dict[str, object] = {"arm": "search", "split": "validation", "search_candidate": candidate.identity,
                                 "case_id": case.identifier, "repeat": repeat, "score": score}
        outcomes.append(row)
        checkpoint()
        return row

    def cases_for(_split: str) -> list[Case]:
        return cases

    state = cast(RunState, cast(object, SimpleNamespace(
        out=tmp_path, lock=threading.RLock(), record=record, outcomes=outcomes, seed=seed,
        contract=SimpleNamespace(harness_phrases=()), statistics=Statistics(family_budget=0.1),
        budget=Budget(200, 3600, reserve=0, approved=True), faults=FaultLatch(threading.RLock()),
        cases_for=cases_for, checkpoint=checkpoint, evaluate_case=evaluate)))

    def finalists() -> Finalists:
        return Finalists(state, SearchPhase(state, edit="prose", search_cases={}, built=lambda item: item),
                         lambda item: item)

    interrupt = False
    stage = finalists()
    for candidate in (seed, *candidates):
        selected = cases if candidate == seed else [case for case in cases if case.identifier in stage.subset]
        for case in selected:
            for repeat in range(2):
                _ = evaluate(candidate, case, "search", repeat=repeat)
        if candidate != seed:
            outcomes.append({"arm": "search", "split": "reflection", "proposed": candidate.identity,
                             "components": candidate.files})
    initial = deepcopy((record, outcomes))
    assert stage.reserved() == 36
    stage.complete()
    assert stage.reserved() == 0
    expected = Leaderboard(state).best_identity()
    assert expected == candidates[1].identity

    record.clear()
    record.update(initial[0])
    outcomes[:] = initial[1]
    interrupt = True
    with pytest.raises(CodedError, match="interrupt C"):
        finalists().complete()
    assert Leaderboard(state).best_identity() == expected
    saved_record, saved_outcomes = snapshots[-1]
    record.clear()
    record.update(saved_record)
    outcomes[:] = saved_outcomes
    interrupt = False
    finalists().complete()
    assert Leaderboard(state).best_identity() == expected
    comparisons = cast(list[dict[str, object]], cast(dict[str, object], record["search"])["comparisons"])
    assert [item["challenger"] for item in comparisons] == [item.identity for item in candidates]


def _interrupt_finalist(target: Path, out: Path, state: Search, case_hash: str, calls: int) -> None:
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2

    def fault(_number: int) -> Exception | None:
        search = read(out / "run.json").get("search")
        if isinstance(search, dict) and cast(dict[str, object], search).get("subset_search_done") is True:
            return CodedError("other-code", "finalist interrupted")
        return None

    state.evaluate_fault = fault
    with pytest.raises(CodedError, match="finalist interrupted"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                factory=state.factory, val_policy="subset")


@pytest.mark.ac("gh-245:AC-3")
def test_finalist_resume_counts_only_new_faults(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _interrupt_finalist(target, out, state, case_hash, calls)
    before = state.search_calls
    assert before > 4
    state.evaluate_fault = lambda number: RuntimeError("provider failed") if number - before in (1, 3, 4) else None
    with pytest.raises(CodedError) as error:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert error.value.code == "search-faulted"
    assert state.search_calls - before == 4


@pytest.mark.ac("gh-245:AC-3")
def test_finalist_resume_rejects_nontext_saved_components(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _interrupt_finalist(target, out, state, case_hash, calls)
    record = read(out / "run.json")
    proposal = next(item for item in cast(list[dict[str, object]], record["outcomes"])
                    if item.get("split") == "reflection" and "components" in item)
    components = cast(dict[str, object], proposal["components"])
    components[next(iter(components))] = 17
    _ = (out / "run.json").write_text(json.dumps(record))
    before = state.search_calls
    state.evaluate_fault = lambda _number: None
    with pytest.raises(CodedError) as error:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert error.value.code == "run-record-tampered"
    assert state.search_calls == before
