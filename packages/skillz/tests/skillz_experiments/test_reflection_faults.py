"""A reflection call that fails is recorded and counted. Repeated failures stop the search with `search-faulted`."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._records import read
from skillz_experiments.run import run
from skillz_experiments.run import _session
from test_run_search import MODEL, _start  # pyright: ignore[reportPrivateUsage]

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
API_REJECTED = "Claude Code fails: api-rejected (exit 1); the invocation counts against the budget"


def _reflections(out: Path) -> list[dict[str, object]]:
    return [row for row in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]) if row.get("split") == "reflection"]


def test_a_failed_reflection_call_is_recorded_with_its_reason_and_proposes_nothing(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: RuntimeError(API_REJECTED) if number == 1 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    failed = [row for row in _reflections(out) if row.get("status") == "failed"]
    assert result["phase"] == "complete"
    assert len(failed) == 1 and failed[0]["reason"] == "api-rejected" and "proposed" not in failed[0]
    assert "api-rejected" in str(failed[0]["failure"])
    assert state.invokes > 1


def test_a_failed_reflection_is_no_attempt_and_does_not_count_as_a_proposal(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: RuntimeError(API_REJECTED) if number == 1 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    attempts = cast(list[dict[str, object]], cast(dict[str, object], result["search"])["attempts"])
    proposed = [row for row in _reflections(out) if "proposed" in row]
    assert len(attempts) == len(proposed) and state.prompts[1].count("proposal 1.") == 1


def test_reflection_calls_that_keep_failing_stop_the_search_with_search_faulted(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_session, "FAULT_STREAK", 2)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda _number: RuntimeError(API_REJECTED)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "search-faulted" and "api-rejected" in str(caught.value)
    assert state.invokes == 2
    assert [row["status"] for row in _reflections(out)] == ["failed", "failed"]
