"""Subset-policy interruptions: a charged fault is accounted, and a resume adds no new proposal."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._records import read
from skillz_experiments.run import run
from test_run_search import MODEL, _allowance, _start, run_search  # pyright: ignore[reportPrivateUsage]
from test_val_policy_finalists import _subset_done  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("gh-245:AC-2")
@pytest.mark.ac("gh-245:AC-3")
def test_interrupted_finalist_resumes_without_new_proposals(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    interrupted = False

    def fault(_number: int) -> Exception | None:
        nonlocal interrupted
        if not interrupted and _subset_done(out):
            interrupted = True
            return CodedError("other-code", "finalist interrupted")
        return None

    state.evaluate_fault = fault
    with pytest.raises(CodedError, match="finalist interrupted"):
        _ = run_search(target, out, state, case_hash, calls, val_policy="subset")
    assert interrupted
    interrupted_record = read(out / "run.json")
    charged = [item for item in cast(list[dict[str, object]], interrupted_record["outcomes"])
               if item.get("arm") == "search" and item.get("split") in ("train", "validation")
               and item.get("search_candidate") != interrupted_record["seed_hash"]]
    assert len(charged) == state.search_calls
    failed = next(item for item in charged if item.get("status") == "evaluation-failed")
    proposals = state.proposals
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete" and state.proposals == proposals
    record = read(out / "run.json")
    rows = cast(list[dict[str, object]], record["outcomes"])
    assert any(item.get("search_candidate") == failed["search_candidate"] and
               item.get("case_id") == failed["case_id"] and item.get("repeat") == failed["repeat"] and
               item.get("status") != "evaluation-failed" for item in rows)
    assert state.search_calls <= _allowance(out)
    assert cast(dict[str, object], record["best_validated"])["identity"] != record["seed_hash"]


@pytest.mark.ac("gh-245:AC-2")
def test_charged_search_error_is_accounted_before_resume(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    interrupted = False

    def fault(_number: int) -> Exception | None:
        nonlocal interrupted
        if not interrupted and not _subset_done(out) and state.proposals and state.search_log[-1][1] == "validation":
            interrupted = True
            return CodedError("other-code", "search interrupted")
        return None

    state.evaluate_fault = fault
    with pytest.raises(CodedError, match="search interrupted"):
        _ = run_search(target, out, state, case_hash, calls, val_policy="subset")
    assert interrupted
    record = read(out / "run.json")
    rows = cast(list[dict[str, object]], record["outcomes"])
    charged = [item for item in rows if item.get("arm") == "search" and item.get("split") in ("train", "validation")
               and item.get("search_candidate") != record["seed_hash"]]
    assert len(charged) == state.search_calls
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.search_calls <= _allowance(out)


@pytest.mark.ac("gh-245:AC-2")
def test_repeated_charged_interruptions_stop_at_search_ceiling(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2

    def fault(_number: int) -> Exception | None:
        return CodedError("other-code", "charged interruption") if _subset_done(out) else None

    state.evaluate_fault = fault
    with pytest.raises(CodedError, match="charged interruption"):
        _ = run_search(target, out, state, case_hash, calls, val_policy="subset")
    ceiling = _allowance(out)
    assert state.search_calls < ceiling
    proposals = state.proposals
    while True:
        previous = state.search_calls
        try:
            result = run(target, out, MODEL, live=True, factory=state.factory)
        except CodedError as stopped:
            assert str(stopped) == "charged interruption" and previous < state.search_calls <= ceiling
        else:
            assert state.search_calls == previous
            break
    assert result["phase"] == "complete" and state.proposals == proposals
    record = read(out / "run.json")
    rows = cast(list[dict[str, object]], record["outcomes"])
    assert sum(item.get("arm") == "search" and item.get("split") in ("train", "validation")
               and item.get("search_candidate") != record["seed_hash"] for item in rows) == state.search_calls
    assert state.search_calls <= ceiling
