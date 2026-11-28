"""Resume and approval edges of the search objective (gh-757)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import run
from skillz_experiments.run._plan import MINIMUM_CANDIDATES

from fake_provider import search_approval
from test_run_search import MODEL
from test_search_objective import TOKENS, _measured, _record, _stop  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


@pytest.mark.ac("gh-757:AC-4")
def test_a_resume_that_repeats_the_recorded_objective_passes_and_the_other_value_is_refused_with_search(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _measured(tmp_path, make_target, write_draft, approvals, statistics=TOKENS)
    fault_at = state.search_calls + 3
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == fault_at else None)
    approval = search_approval(target, out, MODEL, objective="tokens")
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, search=True, approve_budget=approval, objective="tokens",
                factory=state.factory)
    before = (state.invokes, state.search_calls)
    with pytest.raises(CodedError) as refused:
        _ = run(target, out, MODEL, live=True, search=True, objective="quality", factory=state.factory)
    assert refused.value.code == "run-config-differs"
    assert (state.invokes, state.search_calls) == before and _record(out)["objective"] == "tokens"
    result = run(target, out, MODEL, live=True, search=True, objective="tokens", factory=state.factory)
    assert result["phase"] == "complete" and _record(out)["objective"] == "tokens"


@pytest.mark.ac("gh-757:AC-6")
def test_an_approval_one_below_the_shown_calls_asks_again_and_exactly_the_shown_calls_passes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _state = _measured(tmp_path, make_target, write_draft, approvals, families=6, per_family=2)
    asked = _stop(target, out)
    assert asked.code == "budget-unapproved"
    estimate = cast(dict[str, int], asked.data["estimate"])
    assert estimate["expected_candidates"] >= MINIMUM_CANDIDATES
    for approval in (1, estimate["calls"] - 1):
        assert _stop(target, out, approve_budget=approval).code == "budget-unapproved"
        assert _record(out)["plan_mode"] == "baseline"
    assert _stop(target, out, approve_budget=estimate["calls"]).code == "live-required"
    recorded = cast(dict[str, int], _record(out)["estimate"])
    assert recorded["expected_candidates"] >= MINIMUM_CANDIDATES
