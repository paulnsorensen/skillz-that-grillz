"""Subset-policy finalists: full validation before promotion, provider faults, budget stops, and odd counts."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import BudgetExhausted
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments._records import read
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.search import scored_runs, validation_complete
from test_run_search import Search, _allowance, _start, _validation, run_search  # pyright: ignore[reportPrivateUsage]


def _subset_done(out: Path) -> bool:
    search = read(out / "run.json").get("search")
    return isinstance(search, dict) and cast(dict[str, object], search).get("subset_search_done") is True


@pytest.mark.ac("gh-245:AC-2")
def test_qualifying_finalist_completes_full_validation_before_promotion(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    result = run_search(target, out, state, case_hash, calls, val_policy="subset")
    record = read(out / "run.json")
    kept = cast(dict[str, object], record["best_validated"])
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    validation = set(_validation(out))
    scored = {(item["case_id"], item["repeat"]) for item in outcomes
              if item.get("split") == "validation" and item.get("search_candidate") == kept["identity"]}
    assert scored == {(case, repeat) for case in validation for repeat in range(VALIDATION_REPEATS)}
    assert result["phase"] == "complete" and kept["identity"] != record["seed_hash"]


@pytest.mark.ac("gh-245:AC-2")
def test_finalist_provider_error_records_failed_attempt_and_completes_slots(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    faulted = False

    def fault(_number: int) -> Exception | None:
        nonlocal faulted
        if not faulted and _subset_done(out):
            faulted = True
            return OSError("finalist provider failed")
        return None

    state.evaluate_fault = fault
    result = run_search(target, out, state, case_hash, calls, val_policy="subset")
    assert faulted and result["phase"] == "complete"
    record = read(out / "run.json")
    rows = cast(list[dict[str, object]], record["outcomes"])
    failed = next(item for item in rows if item.get("status") == "evaluation-failed" and
                  item.get("arm") == "search" and item.get("split") == "validation")
    assert not any(item.get("search_candidate") == failed["search_candidate"] and
                   item.get("case_id") == failed["case_id"] and item.get("repeat") == failed["repeat"] and
                   item.get("status") != "evaluation-failed" for item in rows)
    validation = set(_validation(out))
    identity = cast(str, failed["search_candidate"])
    assert validation_complete(rows, identity, validation, VALIDATION_REPEATS)
    scored = scored_runs(rows)
    assert cast(int, failed["repeat"]) not in scored.get(identity, {}).get(cast(str, failed["case_id"]), {})
    charged = [item for item in rows if item.get("arm") == "search" and item.get("split") in ("train", "validation")
               and item.get("search_candidate") != record["seed_hash"]]
    assert len(charged) == state.search_calls <= _allowance(out)


@pytest.mark.ac("gh-245:AC-2")
def test_finalist_budget_stop_keeps_only_fully_validated_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    faulted = False

    def fault(_number: int) -> Exception | None:
        nonlocal faulted
        if not faulted and _subset_done(out):
            faulted = True
            return BudgetExhausted("finalist budget spent")
        return None

    state.evaluate_fault = fault
    result = run_search(target, out, state, case_hash, calls, val_policy="subset")
    assert faulted and result["phase"] == "complete"
    record = read(out / "run.json")
    assert cast(dict[str, object], record["search"])["reason"] == "budget-exhausted-seed-retained"
    assert record.get("best_validated") is None
    assert state.search_calls <= _allowance(out)


@pytest.mark.ac("gh-245:AC-2")
def test_odd_validation_count_uses_rounded_up_subset(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=9, per_family=3)
    draft = out / DRAFT_NAME
    cases = cast(list[dict[str, object]], json.loads(draft.read_text()))
    _ = draft.write_text(json.dumps([case for case in cases if case["id"] != "t0-2"]))
    case_hash, calls = approvals(target, out)
    state = Search(target)
    state.score_of = lambda text, split: (0.3 if split == "validation" else 0.8) if text.startswith("improved") else 0.5
    _ = run_search(target, out, state, case_hash, calls, val_policy="subset")
    record = read(out / "run.json")
    validation = _validation(out)
    assert len(validation) % 2 == 1
    proposed = next(item["proposed"] for item in cast(list[dict[str, object]], record["outcomes"])
                    if item.get("split") == "reflection" and isinstance(item.get("proposed"), str))
    calls_for_candidate = [(item["case_id"], item["repeat"]) for item in cast(list[dict[str, object]], record["outcomes"])
                           if item.get("split") == "validation" and item.get("search_candidate") == proposed]
    assert len(calls_for_candidate) == 2 * ((len(validation) + 1) // 2)
    assert {repeat for _, repeat in calls_for_candidate} == {0, 1}
