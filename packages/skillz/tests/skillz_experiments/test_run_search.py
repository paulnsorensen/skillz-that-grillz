"""The search share of a run: GEPA headroom, best validated pick, failed evaluations, and resume."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.harness import Configuration, EnvironmentDiffers, Harness
from skillz_experiments.harness._codex import Codex
from skillz_experiments._intake import intake_contract
from skillz_experiments._records import read
from skillz_experiments._records import write
from skillz_experiments._budget import MAX_CONCURRENT_CALLS, Budget, BudgetExhausted
from skillz_experiments.search import overshoot
from skillz_experiments.run import _session
from skillz_experiments.run import Stop, export, run
from skillz_experiments.run._plan import PREFLIGHT_CALLS
from typing_extensions import override
from fake_provider import FakeProvider, ProviderCrash

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"


@final
class Search:
    """A fake provider that claims the budget like the real harness. Scores are noisy; `improved` text scores best."""

    def __init__(self, target: Path, noise: int = 0) -> None:
        self.contract = intake_contract(target)
        self.noise = noise
        self.lock = threading.Lock()
        self.search_calls = 0
        self.proposals = 0
        self.improved_validated = 0
        self.evaluate_fault: Callable[[int], Exception | None] = lambda _number: None
        self.exhaust_after_improved: int | None = None
        self.fail_holdout = 0
        self.abort_after_improved: int | None = None
        self.padding = 0
        self.drain_search = False
        self.score_of: Callable[[str, str], float | None] | None = None
        self.invokes = 0
        self.invoke_fault: Callable[[int], Exception | None] = lambda _number: None
        self.holdout_calls = 0
        self.holdout_fault: Callable[[int], Exception | None] = lambda _number: None
        self.holdout_case_fault: Callable[[bool, str], Exception | None] = lambda _improved, _case: None
        self.holdout_head_delay = 0.0
        self.search_delay = 0.0
        self.preflight_error: Exception | None = None
        self.close_error: Exception | None = None
        self.suffix = ""
        self.improved_calls = 0

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        return Provider(self, budget, checkpoint)


@final
class Provider(FakeProvider):
    def __init__(self, state: Search, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.state = state
        self.budget = budget
        self.checkpoint = checkpoint

    @override
    def preflight(self) -> dict[str, object]:
        """Claim the live calls once. A resume reuses the recorded pass, as the real harness does."""
        if self.state.preflight_error is not None:
            raise self.state.preflight_error
        if self.budget.calls == 0:
            for _ in range(PREFLIGHT_CALLS):
                self.budget.claim()
        return {"isolation": "test-provider"}

    @override
    def close(self) -> None:
        error, self.state.close_error = self.state.close_error, None
        if error is not None:
            raise error

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        state = self.state
        improved = "improved" in candidate.files["SKILL.md"]
        if improved and not holdout:
            with state.lock:
                state.improved_calls += 1
        if not holdout:
            with state.lock:
                state.search_calls += 1
                number = state.search_calls
                if state.exhaust_after_improved is not None and state.improved_validated >= state.exhaust_after_improved:
                    raise BudgetExhausted("global invocation budget exhausted")
                if state.abort_after_improved is not None and state.improved_validated >= state.abort_after_improved:
                    state.abort_after_improved = None
                    raise CodedError("other-code", "the process stopped")
                if improved and case.split == "validation":
                    state.improved_validated += 1
            if number > 1:
                _ = threading.Event().wait(state.search_delay)
            fault = state.evaluate_fault(number)
            if fault is not None:
                for _ in range(state.contract.calls(case.kind)):
                    self.budget.claim()
                raise fault
        if holdout:
            with state.lock:
                state.holdout_calls += 1
                number = state.holdout_calls
            if number == 1:
                _ = threading.Event().wait(state.holdout_head_delay)
            fault = state.holdout_fault(number)
            if fault is not None:
                raise fault
            case_fault = state.holdout_case_fault(improved, case.identifier)
            if case_fault is not None:
                raise case_fault
        if holdout and state.drain_search:
            with state.lock:
                while state.drain_search and self.budget.calls < self.budget.maximum - self.budget.reserve:
                    self.budget.claim()
                state.drain_search = False
        for _ in range(state.contract.calls(case.kind)):
            self.budget.claim(holdout=holdout)
        if holdout:
            with state.lock:
                failing = state.fail_holdout > 0
                state.fail_holdout -= failing
            if failing:
                raise ProviderCrash("provider fell over")
        self.checkpoint()
        digest = hashlib.sha256(f"{state.noise}{candidate.identity}{case.identifier}".encode()).digest()[0]
        base = 0.7 if improved else 0.2
        score = min(1.0, max(0.0, base + (digest % 3 - 1) * 0.2))
        if state.score_of is not None:
            chosen = state.score_of(candidate.files["SKILL.md"], case.split)
            score = score if chosen is None else chosen
        return {"score": score, "candidate_hash": candidate.identity, "case_hash": case.identifier}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case
        assert schema is not None
        with self.state.lock:
            self.state.invokes += 1
            fault = self.state.invoke_fault(self.state.invokes)
        if fault is not None:
            raise fault
        self.budget.claim(holdout=holdout)
        with self.state.lock:
            self.state.proposals += 1
            number = self.state.proposals
        return {"answer": {name: f"improved {number} " + "x" * self.state.padding + self.state.suffix for name in cast(list[str], schema["required"])}}


def _start(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
           approvals: Callable[..., tuple[str, int]], noise: int = 0,
           families: int = 5) -> tuple[Path, Path, Search, str, int]:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=families)
    case_hash, calls = approvals(target, out)
    return target, out, Search(target, noise), case_hash, calls


@pytest.mark.parametrize("noise", range(5))
def test_a_run_at_the_planned_budget_never_ends_budget_exhausted_seed_retained(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], noise: int) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, noise)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert cast(dict[str, object], result["search"])["reason"] == "validation-mean"
    assert result["phase"] == "complete" and cast(int, read(out / "run.json")["calls"]) <= calls


def test_a_mid_search_exhaustion_keeps_the_best_validated_candidate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.exhaust_after_improved = len(_validation(out))
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    assert search == {"reason": "budget-exhausted-best-validated", "retained_seed": False, "leakage_rejections": {}}
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def _validation(out: Path) -> list[str]:
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    return [str(case["id"]) for case in cases if case["split"] == "validation"]


def test_one_failed_evaluation_scores_zero_and_does_not_abort_the_search(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: RuntimeError("provider fell over") if number == 3 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete"
    assert cast(dict[str, object], result["search"])["reason"] == "validation-mean"
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    failed = [item for item in outcomes if item.get("status") == "evaluation-failed"]
    assert len(failed) == 1 and failed[0]["score"] == 0.0 and failed[0]["failure"] == "provider fell over"


def test_a_resumed_search_spends_at_most_the_remaining_share(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    allowances: list[int] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        allowances.append(cast(int, kwargs["metric_calls"]))
        return real(*args, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == 3 else None)
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert record["phase"] == "prepared"
    planned = cast(int, cast(dict[str, object], record["estimate"])["metric_calls"])
    margin = overshoot(len(_validation(out)))
    spent = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation")
                for item in cast(list[dict[str, object]], record["outcomes"]))
    assert 0 < spent < planned - margin
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    assert allowances == [planned - margin, planned - spent - margin]


def test_a_resume_with_a_spent_search_share_keeps_the_candidate_validated_before_the_abort(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    validation = _validation(out)
    state.abort_after_improved = len(validation)
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    planned = cast(int, cast(dict[str, object], record["estimate"])["metric_calls"])
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    spent = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation") for item in outcomes)
    filler: dict[str, object] = {"arm": "search", "split": "train", "case_id": "filler", "score": 0.0}
    outcomes.extend([filler] * (planned - overshoot(len(validation)) - spent))
    write(out / "run.json", record)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert cast(dict[str, object], result["search"]) == {"reason": "search-share-spent-best-validated", "retained_seed": False, "leakage_rejections": {}}
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def test_a_resume_with_a_real_allowance_keeps_the_pre_abort_candidate_over_worse_later_proposals(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = len(_validation(out))
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    state.score_of = lambda text, _split: 0.0 if "improved" in text and "improved 1 " not in text else None
    proposals_before = state.proposals
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.proposals > proposals_before
    assert cast(dict[str, object], result["search"]) == {"reason": "validation-mean", "retained_seed": False, "leakage_rejections": {}}
    assert "improved 1 " in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def test_a_validated_candidate_below_the_seed_leaves_the_seed_as_the_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (0.1 if split == "validation" else 0.9) if "improved" in text else 0.5
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert "best_validated" in record
    assert cast(dict[str, object], result["search"]) == {"reason": "validation-mean", "retained_seed": True, "leakage_rejections": {}}
    assert cast(dict[str, object], record["gate"])["reason"] == "winner-equals-baseline"


def test_a_validated_candidate_that_ties_the_seed_leaves_the_seed_as_the_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (0.5 if split == "validation" else 0.9) if "improved" in text else 0.5
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    kept = cast(dict[str, object], read(out / "run.json")["best_validated"])
    assert kept["mean"] == pytest.approx(0.5)
    assert cast(dict[str, object], result["search"]) == {"reason": "validation-mean", "retained_seed": True, "leakage_rejections": {}}


def test_an_earlier_validated_candidate_stays_best_over_a_later_worse_one(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

    def table(text: str, split: str) -> float | None:
        if "improved 1 " in text:
            return 0.9 if split == "validation" else 0.5
        return (0.6 if split == "validation" else 1.0) if "improved" in text else None

    state.score_of = table
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    validated = {item["search_candidate"] for item in outcomes if item.get("split") == "validation" and "search_candidate" in item}
    assert len(validated) >= 3
    kept = cast(dict[str, object], record["best_validated"])
    assert "improved 1 " in cast(dict[str, str], kept["files"])["SKILL.md"] and kept["mean"] == pytest.approx(0.9)
    assert "improved 1 " in cast(dict[str, str], record["winner"])["SKILL.md"]


@pytest.mark.parametrize("code", ["isolation-failed", "harness-changed"])
def test_a_coded_reflection_failure_stops_the_search_instead_of_skipping_the_proposal(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], code: str) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: CodedError(code, "a guard fired") if number == 1 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == code
    record = read(out / "run.json")
    assert record["phase"] == "prepared" and record["failure"] == "a guard fired"
    assert state.invokes == 1


def test_a_lasting_reflection_fault_on_the_last_iteration_still_stops_the_run(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def tiny(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        return real(*args, **{**kwargs, "metric_calls": len(cast(list[object], args[3])) + 2})  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", tiny)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda _number: CodedError("isolation-failed", "a guard fired")
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed" and state.invokes >= 1
    record = read(out / "run.json")
    assert record["phase"] == "prepared" and record["failure"] == "a guard fired"


def test_a_task_fault_on_the_first_validation_evaluation_starts_no_further_task_call(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.evaluate_fault = lambda number: CodedError("isolation-failed", "a guard fired") if number == 1 else None
    state.search_delay = 0.1
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert state.search_calls <= 2


def test_a_gate_fault_with_a_slow_head_task_starts_no_further_holdout_call(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.holdout_head_delay = 0.5
    state.holdout_fault = lambda number: CodedError("isolation-failed", "a guard fired") if number == 2 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["phase"] == "searched"
    assert state.holdout_calls == 2


def test_a_transient_reflection_failure_skips_one_proposal_and_the_search_finishes(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: RuntimeError("reflection fell over") if number == 1 else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete" and state.invokes > 1


def test_the_run_record_stays_small_across_many_proposals(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.padding = 20_000
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    kept = cast(dict[str, object], record["best_validated"])
    assert "candidates" not in record and set(kept) == {"identity", "mean", "files"}
    assert state.proposals >= 2
    text = (out / "run.json").read_text()
    stored = {number for number in range(1, state.proposals + 1) if f"improved {number} " in text}
    assert len(stored) == 1


def test_a_failed_holdout_call_resumes_and_finishes_within_the_approved_budget(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    state.drain_search = True
    with pytest.raises(ProviderCrash, match="fell over"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert read(out / "run.json")["phase"] == "searched"
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete" and cast(int, read(out / "run.json")["calls"]) <= calls
    assert cast(dict[str, object], result["estimate"])["holdout_retry_calls"] == 2 * state.contract.calls("echo")


def test_a_gate_that_cannot_finish_stops_with_gate_budget_exhausted(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 10_000
    with pytest.raises(ProviderCrash):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    stop: Stop | None = None
    for _attempt in range(100):
        try:
            _ = run(target, out, MODEL, live=True, factory=state.factory)
        except Stop as error:
            stop = error
            break
        except ProviderCrash:
            continue
    assert stop is not None
    assert stop.code == "gate-budget-exhausted" and "new run" in str(stop.data["next"])
    assert cast(int, read(out / "run.json")["calls"]) <= calls


def test_a_different_coded_error_aborts_the_search_instead_of_scoring_zero(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: CodedError("other-code", "a coded stop") if number == 3 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "other-code"
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert record["phase"] == "prepared" and not any(item.get("status") == "evaluation-failed" for item in outcomes)


@pytest.mark.parametrize("code", ["isolation-failed", "harness-changed"])
def test_an_isolation_or_identity_failure_aborts_the_search_instead_of_scoring_zero(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], code: str) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: CodedError(code, "a guard fired") if number == 4 else None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == code
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert record["phase"] == "prepared" and record["failure"] == "a guard fired"
    assert not any(item.get("status") == "evaluation-failed" for item in outcomes)


def test_a_changed_harness_raises_the_harness_changed_code() -> None:
    harness = object.__new__(Harness)
    harness.configuration = cast(object, _Identity())  # pyright: ignore[reportAttributeAccessIssue]
    harness.identity = cast(dict[str, object], {"changed": True})
    with pytest.raises(CodedError) as caught:
        harness._unchanged()  # pyright: ignore[reportPrivateUsage]
    assert caught.value.code == "harness-changed"


def test_a_search_where_every_evaluation_fails_stops_with_search_failed(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda _number: RuntimeError("provider fell over")
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "search-failed" and "new run directory" in str(caught.value)
    assert read(out / "run.json")["phase"] == "prepared"


class _Identity:
    def identity(self) -> dict[str, object]:
        return {}


class _Task:
    def __init__(self) -> None:
        self.seen: list[Candidate] = []

    def check_candidate(self, candidate: Candidate) -> bool:
        self.seen.append(candidate)
        return False


def test_harness_check_candidate_delegates_to_the_task_transport(
        tmp_path: Path, make_target: Callable[..., Path]) -> None:
    seed = Candidate.capture(make_target(tmp_path), [], intake_contract(make_target(tmp_path / "other")))
    task = _Task()
    harness = object.__new__(Harness)
    harness.configuration = cast(object, _Identity())  # pyright: ignore[reportAttributeAccessIssue]
    harness.identity = {}
    harness.transports = cast(dict[str, object], {"task": task})  # pyright: ignore[reportAttributeAccessIssue]
    assert harness.check_candidate(seed) is False
    assert task.seen == [seed]


def test_a_run_stopped_by_an_isolation_failure_refuses_to_resume_and_makes_no_live_call(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda _number: CodedError("isolation-failed", "a guard fired")
    with pytest.raises(CodedError) as first:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = read(out / "run.json")
    assert first.value.code == "isolation-failed" and recorded["failure_code"] == "isolation-failed"
    assert "preflight" not in recorded
    before = (state.invokes, state.search_calls, recorded["calls"])
    for live in (True, False):
        with pytest.raises(Stop) as second:
            _ = run(target, out, MODEL, live=live, factory=state.factory)
        assert second.value.code == "run-terminated" and "new run" in str(second.value.data["next"])
    assert (state.invokes, state.search_calls, read(out / "run.json")["calls"]) == before


def test_a_credential_change_found_when_the_provider_closes_a_complete_run_keeps_the_result_and_warns(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.close_error = CodedError("credential-changed", "the login file changed")
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    warning = {"code": "credential-changed", "message": "the login file changed"}
    exported_warning = {"code": "credential-changed", "hint": "log in again before the next run"}
    recorded = read(out / "run.json")
    assert result["phase"] == "complete" and result["close_warning"] == exported_warning
    assert recorded["close_warning"] == warning and "failure_code" not in recorded
    before = (state.invokes, state.search_calls, recorded["calls"])
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"
    assert (state.invokes, state.search_calls, read(out / "run.json")["calls"]) == before
    destination = tmp_path / "exported"
    assert export(out, destination)["installed"] is False
    assert read(destination / "report.json")["close_warning"] == exported_warning
    assert "login file" not in (destination / "report.json").read_text()


@pytest.mark.parametrize("code", ["credential-rotated", "credential-refreshed"])
def test_a_handled_login_change_on_close_keeps_the_result_and_asks_for_no_login(
        code: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.close_error = CodedError(code, "the login changed and the run handled it")
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert result["phase"] == "complete"
    assert result["close_warning"] == {"code": code, "hint": "no action needed"}
    assert "failure_code" not in read(out / "run.json")


def test_a_run_called_inside_an_except_block_does_not_mistake_the_caller_error_for_its_own(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.close_error = CodedError("credential-changed", "the login file changed")
    try:
        raise KeyError("caller")
    except KeyError as caller:
        result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                     factory=state.factory)
        assert not getattr(caller, "__notes__", [])
    recorded = read(out / "run.json")
    assert result["phase"] == "complete" and recorded["close_warning"] == {"code": "credential-changed", "message": "the login file changed"}
    assert "failure_code" not in recorded and "close_failure" not in recorded
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_credential_change_found_on_close_after_a_failure_keeps_the_failure_and_ends_the_run(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    state.close_error = CodedError("credential-changed", "the login file changed")
    with pytest.raises(ProviderCrash) as first:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = read(out / "run.json")
    assert recorded["failure"] == str(first.value) and recorded["failure"] != "the login file changed"
    assert recorded["close_failure"] == {"code": "credential-changed", "message": "the login file changed"}
    assert recorded["failure_code"] == "credential-changed" and "preflight" not in recorded
    assert any("credential-changed" in note for note in first.value.__notes__)
    with pytest.raises(Stop) as second:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert second.value.code == "run-terminated"


@pytest.mark.parametrize("code", ["credential-rotated", "credential-refreshed"])
def test_a_handled_login_change_on_close_after_a_failure_does_not_end_the_run(
        code: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    state.close_error = CodedError(code, "the login changed and the run handled it")
    with pytest.raises(ProviderCrash):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = read(out / "run.json")
    assert recorded["close_failure"] == {"code": code, "message": "the login changed and the run handled it"}
    assert "failure_code" not in recorded
    state.fail_holdout = 0
    state.close_error = None
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_failed_sandbox_preflight_leaves_the_run_fixable_and_a_later_run_goes_ahead(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.preflight_error = CodedError("sandbox-unavailable", "socat is missing")
    with pytest.raises(CodedError) as first:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert first.value.code == "sandbox-unavailable"
    assert "preflight" not in read(out / "run.json")
    state.preflight_error = None
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_budget_stop_and_an_isolation_fault_in_one_batch_end_the_run_before_the_gate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

    def fault(number: int) -> Exception | None:
        _ = threading.Event().wait(0.1 if number == 1 else 0.4)
        if number == 1:
            return BudgetExhausted("global invocation budget exhausted")
        return CodedError("isolation-failed", "a guard fired") if number == 2 else None

    state.evaluate_fault = fault
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["failure_code"] == "isolation-failed"
    assert state.holdout_calls == 0


@pytest.mark.parametrize("early", ["sandbox-unavailable", "harness-changed"])
def test_a_terminal_fault_beats_an_earlier_non_terminal_fault_in_one_batch(
        early: str, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

    def fault(number: int) -> Exception | None:
        _ = threading.Event().wait(0.1 if number == 1 else 0.4)
        if number == 1:
            return CodedError(early, "an early fault")
        return CodedError("isolation-failed", "a guard fired") if number == 2 else None

    state.evaluate_fault = fault
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["failure_code"] == "isolation-failed"
    with pytest.raises(Stop) as second:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert second.value.code == "run-terminated"


def test_a_late_transient_gate_failure_before_an_isolation_fault_still_records_the_isolation_code(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.holdout_head_delay = 0.3
    state.holdout_fault = lambda number: (RuntimeError("provider fell over") if number == 1 else
                                          CodedError("isolation-failed", "a guard fired") if number == 2 else None)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "isolation-failed"
    assert read(out / "run.json")["failure_code"] == "isolation-failed"
    held = state.holdout_calls
    with pytest.raises(Stop) as second:
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert second.value.code == "run-terminated" and state.holdout_calls == held


def test_a_transient_gate_failure_records_no_terminal_code(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.fail_holdout = 1
    with pytest.raises(ProviderCrash):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert "failure_code" not in read(out / "run.json")
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def test_a_run_stopped_by_another_coded_error_still_resumes(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 1 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"


def _failed_holdout(record: dict[str, object]) -> list[dict[str, object]]:
    return [item for item in cast(list[dict[str, object]], record["outcomes"])
            if item.get("split") == "holdout" and item.get("status") == "evaluation-failed"]


def test_a_provider_fault_on_every_winner_repeat_of_one_holdout_case_drops_that_case_and_the_gate_finishes(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    victim: list[str] = []

    def fault(improved: bool, case: str) -> Exception | None:
        with state.lock:
            if improved and not victim:
                victim.append(case)
        return RuntimeError("transport fell over") if improved and case == victim[0] else None

    state.holdout_case_fault = fault
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    gate = cast(dict[str, object], record["gate"])
    failed = _failed_holdout(record)
    assert result["phase"] == "complete" and victim
    assert {(item["arm"], item["case_id"]) for item in failed} == {("winner", victim[0])}
    assert sorted(cast(int, item["repeat"]) for item in failed) == list(range(cast(int, gate["repeats"])))
    scored = {item["case_id"] for item in cast(list[dict[str, object]], record["outcomes"])
              if item.get("split") == "holdout" and item.get("status") != "evaluation-failed"}
    assert gate["n_shared"] == len(scored | {victim[0]}) - 1 == gate["cases"]
    moves = cast(dict[str, list[str]], gate["moves"])
    assert set(moves) == {"fixed", "broke", "unresolved"}
    assert victim[0] not in [case for ids in moves.values() for case in ids]
    assert "se_floor" in gate


def test_a_dropped_holdout_case_below_min_coverage_makes_the_gate_low_coverage(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    victim: list[str] = []

    def fault(improved: bool, case: str) -> Exception | None:
        with state.lock:
            if improved and not victim:
                victim.append(case)
        return RuntimeError("transport fell over") if improved and case == victim[0] else None

    state.holdout_case_fault = fault
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                 factory=state.factory, statistics={"min_coverage": 1.0})
    gate = cast(dict[str, object], read(out / "run.json")["gate"])
    assert result["phase"] == "complete" and victim
    assert victim[0] not in [case for ids in cast(dict[str, list[str]], gate["moves"]).values() for case in ids]
    assert gate["verdict"] == "inconclusive" and cast(list[str], gate["reasons"])[0] == "low-coverage"


def test_a_resume_retries_a_failed_holdout_repeat_once_and_uses_the_new_score(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.holdout_fault = lambda number: (RuntimeError("transport fell over") if number == 1 else
                                          ProviderCrash("stop the gate") if number == 2 else None)
    with pytest.raises(ProviderCrash):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    (failed,) = _failed_holdout(read(out / "run.json"))
    state.holdout_fault = lambda _number: None
    result = run(target, out, MODEL, live=True, factory=state.factory)
    record = read(out / "run.json")
    triple = (failed["arm"], failed["case_id"], failed["repeat"])
    retried = [item for item in cast(list[dict[str, object]], record["outcomes"])
               if (item.get("arm"), item.get("case_id"), item.get("repeat")) == triple]
    assert result["phase"] == "complete" and cast(int, record["calls"]) <= calls
    assert [item.get("status") for item in retried][0] == "evaluation-failed" and len(retried) == 2
    assert retried[1].get("status") != "evaluation-failed"
    assert len(_failed_holdout(record)) == 1
    assert cast(dict[str, object], record["gate"])["n_shared"] == cast(dict[str, object], record["gate"])["cases"]


def test_a_full_codex_run_ends_model_unsupported_after_the_first_rejected_call(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    """A real `Codex.invoke` behind a real `Harness` and `run`: the first rejection ends the run, not `search-failed`."""
    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    log = tmp_path / "codex.log"
    fake = tmp_path / "bin/codex"
    fake.parent.mkdir()
    message = "The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account."
    _ = fake.write_text("#!/bin/sh\n" + f"echo call >> {log}\n" + "cat >/dev/null\n"
                        + "echo '{\"type\":\"error\",\"message\":\"" + message + "\"}'\n" + "exit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))

    def preflight(self: Codex) -> dict[str, object]:
        del self
        return {"codex_version": "codex-cli 0.160.0", "isolation": "passed", "live_calls": 0, "environment_hash": "e1"}

    monkeypatch.setattr(Codex, "preflight", preflight)

    def accept(self: Codex, candidate: Candidate) -> bool:
        del self, candidate
        return True

    monkeypatch.setattr(Codex, "check_candidate", accept)
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    configuration = Configuration.single("codex", MODEL)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                configuration=configuration)
    assert caught.value.code == "model-unsupported"
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert record["failure_code"] == "model-unsupported" and record["phase"] == "prepared"
    assert not any(item.get("status") == "evaluation-failed" for item in outcomes)
    assert log.read_text().count("call") <= MAX_CONCURRENT_CALLS, "later search calls ran after the first rejection"


SPAN = "summarise the quarterly ledger totals and flag every unusual"
ANSWER = "the quarterly total is exactly four hundred dollars"
FIXTURE = "ledger-fixture.md"


def _leak_start(tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]],
                seed_extra: str = "") -> tuple[Path, Path, Search, str, int]:
    """Start a run whose first case holds a long request, a long expected answer, and a named fixture."""
    target, out = make_target(tmp_path), tmp_path / "run"
    if seed_extra:
        with (target / "SKILL.md").open("a", encoding="utf-8") as handle:
            _ = handle.write("\n" + seed_extra + "\n")
    cases: list[dict[str, object]] = []
    for family in range(5):
        for index in range(2):
            name = f"t{family}-{index}"
            cases.append({"id": name, "family": f"f{family}", "kind": "task", "request": f"please {SPAN} payment {name} today",
                          "files": {FIXTURE: "hello\n"}, "expected": {"answer": ANSWER}, "source": "skill"})
    out.mkdir(mode=0o700, parents=True)
    _ = (out / "cases.draft.json").write_text(json.dumps(cases), encoding="utf-8")
    case_hash, calls = approvals(target, out)
    return target, out, Search(target), case_hash, calls


def _spy_feedback(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, dict[str, object]]]:
    seen: list[tuple[float, dict[str, object]]] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        evaluate = cast(Callable[[dict[str, str], object], tuple[float, dict[str, object]]], args[4])

        def recording(components: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
            result = evaluate(components, example)
            seen.append(result)
            return result
        return real(args[0], args[1], args[2], args[3], recording, args[5], **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    return seen


@pytest.mark.parametrize(("suffix", "rule"), [
    (" " + SPAN, "request-span"), (" " + ANSWER, "expected-answer"),
    (" see case t1-1 here", "case-id"), (f" open {FIXTURE} first", "fixture-name")])
def test_a_leaking_proposal_scores_zero_without_a_task_or_judge_call_and_is_counted(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch, suffix: str, rule: str) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = suffix
    seen = _spy_feedback(monkeypatch)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    rejections = cast(dict[str, int], cast(dict[str, object], result["search"])["leakage_rejections"])
    assert state.proposals > 0 and state.improved_calls == 0
    assert list(rejections) == [rule] and 0 < rejections[rule] <= state.proposals
    rejected = [(score, feedback) for score, feedback in seen if feedback.get("reason") == "candidate-leakage-rejected"]
    assert rejected
    for score, feedback in rejected:
        assert score == 0.0
        assert set(feedback) == {"task_correct", "request", "rejected", "reason", "rule"} and feedback["rule"] == rule
        assert ANSWER not in json.dumps({key: value for key, value in feedback.items() if key != "request"})
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert all("improved" not in str(item) for item in outcomes)
    assert cast(dict[str, object], record["search"])["leakage_rejections"] == rejections


def test_a_run_record_and_export_hold_the_counts_but_no_denylist_text(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " " + ANSWER
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert cast(dict[str, object], result["search"])["leakage_rejections"]
    text = (out / "run.json").read_text(encoding="utf-8")
    assert ANSWER not in text and SPAN not in text and "denylist" not in text
    destination = tmp_path / "exported"
    _ = export(out, destination)
    report = read(destination / "report.json")
    assert cast(dict[str, object], report["search"])["leakage_rejections"] == cast(dict[str, object], result["search"])["leakage_rejections"]
    assert ANSWER not in (destination / "report.json").read_text(encoding="utf-8")


def test_leakage_counts_survive_a_fault_and_resume_and_the_record_holds_only_digests(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " " + ANSWER
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 2 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    stopped = read(out / "run.json")
    assert stopped["phase"] == "prepared" and "search" not in stopped
    before = cast(dict[str, list[str]], stopped["leakage_rejected"])
    assert list(before) == ["expected-answer"] and before["expected-answer"]
    result = run(target, out, MODEL, live=True, factory=state.factory)
    record = read(out / "run.json")
    after = cast(dict[str, list[str]], record["leakage_rejected"])
    counts = cast(dict[str, int], cast(dict[str, object], result["search"])["leakage_rejections"])
    assert set(before["expected-answer"]) < set(after["expected-answer"])
    assert counts == {"expected-answer": len(after["expected-answer"])}
    text = (out / "run.json").read_text(encoding="utf-8")
    assert ANSWER not in text and SPAN not in text and "denylist" not in text


def test_a_provider_that_checkpoints_in_its_constructor_opens_the_session(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)

    def factory(model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        checkpoint()
        return state.factory(model, budget, checkpoint)

    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=factory)
    assert result["phase"] == "complete" and read(out / "run.json")["leakage_rejected"] == {}


def test_a_proposal_that_repeats_text_the_seed_already_holds_is_not_rejected(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals, seed_extra=SPAN)
    state.suffix = " " + SPAN
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert cast(dict[str, object], result["search"])["leakage_rejections"] == {}
    assert state.improved_calls > 0
