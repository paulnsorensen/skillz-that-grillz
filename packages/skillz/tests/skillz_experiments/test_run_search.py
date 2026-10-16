"""The search share of a run: GEPA headroom, best validated pick, failed evaluations, and resume."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.harness import Configuration, EnvironmentDiffers, Harness
from skillz_experiments.harness._codex import Codex
from skillz_experiments.harness._command import _validate_answer  # pyright: ignore[reportPrivateUsage]
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
        self.search_log: list[tuple[str, str, str]] = []
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
        self.prompts: list[str] = []
        self.edit_offset: int | None = None
        self.refuse = False
        self.memos: list[object] = []
        self.fixed_number: int | None = None
        self.hypothesis_of: Callable[[int], str | None] = lambda _number: None
        self.omit_hypothesis = False

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

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        state = self.state
        with state.lock:
            state.memos.append(checks)
        improved = "improved" in candidate.files["SKILL.md"]
        if improved and not holdout:
            with state.lock:
                state.improved_calls += 1
        if not holdout:
            with state.lock:
                state.search_calls += 1
                number = state.search_calls
                state.search_log.append((candidate.identity, case.split, case.identifier))
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
        del candidate, case
        assert schema is not None
        with self.state.lock:
            self.state.prompts.append(prompt)
            self.state.invokes += 1
            fault = self.state.invoke_fault(self.state.invokes)
        if fault is not None:
            raise fault
        self.budget.claim(holdout=holdout, role="reflection")
        with self.state.lock:
            self.state.proposals += 1
            number = self.state.proposals
        if self.state.refuse:
            return {"answer": {"not-a-component": "x"}}
        if self.state.edit_offset is not None:
            # The stated budget plus the offset: a new line after that many parent lines is one edit each.
            parent = cast(dict[str, dict[str, str]], json.loads(prompt.split("\n", 1)[1]))["candidate"]
            edits = _budgets([prompt])[0] + self.state.edit_offset
            return {"answer": {name: "".join(f"{line}\nimproved {number}\n" if index < edits else f"{line}\n"
                                             for index, line in enumerate(text.splitlines()))
                               for name, text in parent.items()} | {"@hypothesis": self.state.hypothesis_of(number)}}
        texts = {name: f"improved {self.state.fixed_number or number} " + "x" * self.state.padding + self.state.suffix
                 for name in cast(list[str], schema["required"]) if name != "@hypothesis"}
        if self.state.omit_hypothesis:
            _validate_answer(texts, schema)
            return {"answer": texts}
        return {"answer": texts | {"@hypothesis": self.state.hypothesis_of(number)}}


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
    # The summary joins the fault accounting and the three claims.
    assert {"retries", "calls_by_role", "seconds_by_role", "claims", "replay"} <= set(result)
    assert {"scored", "failed", "failed_by_reason"} <= set(cast(dict[str, object], result["search"]))


def test_a_mid_search_exhaustion_keeps_the_best_validated_candidate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.exhaust_after_improved = len(_validation(out))
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert _core(result) == {
        "reason": "budget-exhausted-best-validated", "retained_seed": False, "leakage_rejections": {}, "proposal_over_budget": 0}
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]

def _core(result: dict[str, object]) -> dict[str, object]:
    """Return the search fields that the attempt record and the evaluation counts did not add. `test_search_faults.py` covers the counts."""
    search = cast(dict[str, object], result["search"])
    assert set(search) - {"scored", "failed", "failed_by_reason"} == {"reason", "retained_seed", "leakage_rejections", "proposal_over_budget", "attempts", "text_dropped"}
    return {key: search[key] for key in ("reason", "retained_seed", "leakage_rejections", "proposal_over_budget")}



def _budgets(prompts: list[str]) -> list[int]:
    return [int(prompt.split("Make at most ", 1)[1].split(" ", 1)[0]) for prompt in prompts]


LAST_PROPOSAL = "This may be the last proposal, so prefer one small, safe change."


def _allowance(out: Path) -> int:
    """Return the GEPA call budget: the planned calls less the overshoot margin. The last iteration can pass it by up to that margin."""
    planned = cast(int, cast(dict[str, object], read(out / "run.json")["estimate"])["metric_calls"])
    return planned - overshoot(len(_validation(out)))


@pytest.mark.ac("gh-239:AC-1")
@pytest.mark.ac("gh-239:AC-3")
def test_each_reflection_prompt_states_a_shrinking_budget_and_its_progress_and_a_small_edit_is_not_counted(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    heads = [prompt.split("\n", 1)[0] for prompt in state.prompts]
    allowance = _allowance(out)
    assert len(heads) == state.proposals >= 3
    assert all(f"this is proposal {number}." in head for number, head in enumerate(heads, 1))
    assert all(f"of {allowance} (" in head for head in heads)
    budgets = _budgets(state.prompts)
    assert budgets[0] in (5, 6) and budgets == sorted(budgets, reverse=True) and budgets[-1] < budgets[0]
    assert cast(dict[str, object], result["search"])["proposal_over_budget"] == 0


@pytest.mark.ac("gh-239:AC-4")
def test_the_last_proposal_of_a_normal_run_states_the_last_proposal_sentence_and_the_smallest_budget(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert _budgets(state.prompts)[-1] == 1
    assert LAST_PROPOSAL in state.prompts[-1].split("\n", 1)[0]
    assert LAST_PROPOSAL not in state.prompts[0].split("\n", 1)[0]


@pytest.mark.ac("gh-239:AC-2")
@pytest.mark.parametrize(("offset", "counted"), [(0, False), (1, True)])
def test_only_a_proposal_with_more_edits_than_the_budget_is_counted(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], offset: int, counted: bool) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.edit_offset = offset
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    over = cast(int, cast(dict[str, object], result["search"])["proposal_over_budget"])
    assert state.improved_calls > 0 and state.proposals >= 3
    assert over == (state.proposals if counted else 0)
    assert "proposal_over_budget" not in read(out / "run.json")
    assert cast(dict[str, object], result["search"])["reason"] == "validation-mean"


@pytest.mark.ac("gh-239:AC-2")
def test_the_over_budget_count_survives_a_resume(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.edit_offset = 1
    state.abort_after_improved = len(_validation(out))
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    before = state.proposals
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    assert before > 0 and sum(item.get("over_budget") is True for item in outcomes) == before
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.proposals > before
    assert cast(dict[str, object], result["search"])["proposal_over_budget"] == state.proposals


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
    # GEPA counts each reused outcome as a metric call, so the grant adds the `spent` reused outcomes back.
    assert allowances == [planned - margin, planned - margin]


def _search_pairs(record: dict[str, object]) -> list[tuple[str, str, str]]:
    """Return the (candidate identity, split, case) of each recorded search evaluation that scored."""
    return [(cast(str, item["search_candidate"]), cast(str, item["split"]), cast(str, item["case_id"]))
            for item in cast(list[dict[str, object]], record["outcomes"])
            if item.get("arm") == "search" and item.get("split") in ("train", "validation")
            and "search_candidate" in item and item.get("status") != "evaluation-failed"]


def _interrupt(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
               approvals: Callable[..., tuple[str, int]]) -> tuple[Path, Path, Search, int]:
    """Stop a run on its third search call. Return the target, the run directory, the provider state, and the planned calls."""
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == 3 else None)
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    return target, out, state, cast(int, cast(dict[str, object], read(out / "run.json")["estimate"])["metric_calls"])


@pytest.mark.ac("gh-279:AC-1")
def test_a_resumed_search_makes_no_call_for_a_candidate_and_case_that_the_record_holds(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, _ = _interrupt(tmp_path, make_target, write_draft, approvals)
    recorded = _search_pairs(read(out / "run.json"))
    assert recorded
    state.search_log.clear()
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    assert not set(recorded) & set(state.search_log)
    # The record keeps each reused outcome once.
    assert Counter(_search_pairs(read(out / "run.json")))[recorded[0]] == 1


@pytest.mark.ac("gh-279:AC-2")
def test_a_resumed_search_gets_the_metric_calls_that_the_record_left_plus_the_outcomes_it_reuses(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    grants: list[int] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        grants.append(cast(int, kwargs["metric_calls"]))
        return real(*args, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    target, out, state, planned = _interrupt(tmp_path, make_target, write_draft, approvals)
    record = read(out / "run.json")
    spent = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation")
                for item in cast(list[dict[str, object]], record["outcomes"]))
    reusable = len(_search_pairs(record))
    margin = overshoot(len(_validation(out)))
    state.search_log.clear()
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    # GEPA counts a reused outcome as a metric call, so the engine bound adds each reusable outcome to the share that the record left.
    assert grants == [planned - margin, planned - margin - spent + reusable]
    assert len(state.search_log) <= planned - spent


@pytest.mark.ac("gh-279:AC-3")
def test_a_run_without_an_interrupt_calls_the_model_once_for_each_recorded_search_outcome(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    grants: list[int] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        grants.append(cast(int, kwargs["metric_calls"]))
        return real(*args, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert grants == [_allowance(out)]
    assert Counter(state.search_log) == Counter(_search_pairs(read(out / "run.json")))


def test_a_resume_evaluates_a_recorded_failed_evaluation_again(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, _ = _interrupt(tmp_path, make_target, write_draft, approvals)
    record = read(out / "run.json")
    failed = next(item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("arm") == "search")
    failed |= {"status": "evaluation-failed", "score": 0.0, "failure": "provider fell over"}
    write(out / "run.json", record)
    pair = (cast(str, failed["search_candidate"]), cast(str, failed["split"]), cast(str, failed["case_id"]))
    state.search_log.clear()
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert pair in state.search_log


def test_a_second_interrupt_and_resume_calls_no_pair_that_either_earlier_process_recorded(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, _ = _interrupt(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == 6 else None)
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, factory=state.factory)
    recorded = _search_pairs(read(out / "run.json"))
    assert len(recorded) > 2
    state.search_log.clear()
    state.evaluate_fault = lambda _number: None
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    validated = {pair for pair in recorded if pair[1] == "validation"}
    assert validated and not validated & set(state.search_log)
    # A reused outcome stays in the record once. Only a real call adds one.
    final = Counter(_search_pairs(read(out / "run.json")))
    assert all(final[pair] >= count for pair, count in Counter(recorded).items())
    assert sum(final.values()) == len(recorded) + len(state.search_log)


def test_a_resume_after_a_validated_proposal_calls_no_validation_pair_that_the_record_holds(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = len(_validation(out))
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = _search_pairs(read(out / "run.json"))
    validated = {pair for pair in recorded if pair[1] == "validation"}
    assert len({identity for identity, _, _ in validated}) >= 2
    state.search_log.clear()
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.search_log and not validated & set(state.search_log)
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


@pytest.mark.ac("gh-279:AC-2")
def test_a_resume_with_recorded_outcomes_that_the_search_never_asks_for_again_makes_no_live_call_above_the_share_that_the_record_left(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = len(_validation(out))
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    planned = cast(int, cast(dict[str, object], record["estimate"])["metric_calls"])
    margin = overshoot(len(_validation(out)))
    # A live model proposes new candidates on resume, so the outcomes of an old candidate are never asked for again.
    ghosts = 3 * margin
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    outcomes.extend({"arm": "search", "split": "train", "case_id": f"ghost-{number}", "score": 0.0,
                     "search_candidate": "ghost"} for number in range(ghosts))
    write(out / "run.json", record)
    state.search_log.clear()
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.search_log
    total = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation")
                for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]))
    # The last iteration can pass the search ceiling by at most the overshoot margin, so the total stays within the plan.
    assert total <= planned


def test_a_resumed_identical_proposal_keeps_the_gain_of_the_candidate_that_the_interrupt_cut_short(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = 1
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    state.fixed_number = 1
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    record = read(out / "run.json")
    observations = _session.stall_observations(cast(list[dict[str, object]], record["outcomes"]), set(_validation(out)))
    # The first window ends at the interrupt. The second window holds the same proposal, which now completes its validation.
    assert len(observations) >= 2 and observations[1] is not None and observations[1] > 0


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
    assert _core(result) == {"reason": "search-share-spent-best-validated", "retained_seed": False, "leakage_rejections": {}, "proposal_over_budget": 0}
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
    # Every later proposal scores 0, so the stall stop ends the search.
    assert _core(result) == {"reason": "converged-stalled", "retained_seed": False, "leakage_rejections": {}, "proposal_over_budget": 0}
    assert "improved 1 " in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def test_a_validated_candidate_below_the_seed_leaves_the_seed_as_the_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (0.1 if split == "validation" else 0.9) if "improved" in text else 0.5
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert "best_validated" in record
    assert _core(result) == {"reason": "validation-mean", "retained_seed": True, "leakage_rejections": {}, "proposal_over_budget": 0}
    assert "reason" not in cast(dict[str, object], record["gate"])


def test_a_validated_candidate_that_ties_the_seed_leaves_the_seed_as_the_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (0.5 if split == "validation" else 0.9) if "improved" in text else 0.5
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    kept = cast(dict[str, object], read(out / "run.json")["best_validated"])
    assert kept["mean"] == pytest.approx(0.5)
    assert _core(result) == {"reason": "validation-mean", "retained_seed": True, "leakage_rejections": {}, "proposal_over_budget": 0}


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


def test_a_search_where_every_evaluation_fails_stops_with_search_faulted(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.evaluate_fault = lambda _number: RuntimeError("provider fell over")
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "search-faulted" and "resume the run in the same directory" in str(caught.value)
    assert "new run directory" not in str(caught.value)
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
            if item.get("split") == "holdout" and item.get("arm") != "replay"
            and item.get("status") == "evaluation-failed"]


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
    target, out, state, _, _ = _start(tmp_path, make_target, write_draft, approvals)
    flags = {"min_coverage": 1.0}
    case_hash, calls = approvals(target, out, statistics=flags)
    victim: list[str] = []

    def fault(improved: bool, case: str) -> Exception | None:
        with state.lock:
            if improved and not victim:
                victim.append(case)
        return RuntimeError("transport fell over") if improved and case == victim[0] else None

    state.holdout_case_fault = fault
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                 factory=state.factory, statistics=flags)
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
        assert set(feedback) == {"task_correct", "request", "case_id", "rejected", "reason", "rule"} and feedback["rule"] == rule
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


STALL = "Proposals since the last validation gain: "
WARN = "Try a different kind of change."
SHIFT = "Change a different component or section."


def _stalls(text: str, _split: str) -> float:
    """Score the first proposal above the seed. Score every later proposal at 0, so the search stalls from proposal 2 on."""
    if "improved 1 " in text:
        return 0.9
    return 0.0 if "improved" in text else 0.5


def _stall_counts(prompts: list[str]) -> list[int]:
    return [int(prompt.split(STALL, 1)[1].split(".", 1)[0]) for prompt in prompts]


def _search_spent(out: Path) -> int:
    return sum(item.get("arm") == "search" and item.get("split") in ("train", "validation")
               for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]))


@pytest.mark.ac("gh-240:AC-4")
def test_a_search_that_stalls_for_eight_proposals_stops_converged_stalled_and_still_runs_the_gate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = _stalls
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert state.proposals == 9 and _search_spent(out) < _allowance(out)
    assert cast(dict[str, object], result["search"])["reason"] == "converged-stalled"
    assert result["phase"] == "complete" and state.holdout_calls > 0
    assert "improved 1 " in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def test_each_reflection_prompt_states_the_stall_count_and_gives_advice_from_warn_on(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = _stalls
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    heads = [prompt.split("\n", 1)[0] for prompt in state.prompts]
    assert _stall_counts(heads) == [0, 0, 1, 2, 3, 4, 5, 6, 7]
    assert [WARN in head for head in heads] == [False] * 4 + [True] * 2 + [False] * 3
    assert [SHIFT in head for head in heads] == [False] * 6 + [True] * 3


@pytest.mark.ac("gh-240:AC-5")
def test_a_resumed_run_rebuilds_the_stall_count_from_the_record_and_stops_on_time(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = _stalls
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 6 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert state.proposals == 5
    result = run(target, out, MODEL, live=True, factory=state.factory)
    # The sixth prompt failed before its answer; the resumed prompts continue its count.
    assert _stall_counts([prompt.split("\n", 1)[0] for prompt in state.prompts]) == [0, 0, 1, 2, 3, 4, 4, 5, 6, 7]
    assert state.proposals == 9
    assert cast(dict[str, object], result["search"])["reason"] == "converged-stalled"


@pytest.mark.ac("gh-240:AC-5")
def test_a_resume_after_the_stall_stop_skips_the_search(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = _stalls
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    # A stop right before the `searched` checkpoint leaves the record in the `prepared` phase.
    record = read(out / "run.json")
    record["phase"] = "prepared"
    write(out / "run.json", record)
    searched: list[bool] = []

    def never(*_args: object, **_kwargs: object) -> None:
        searched.append(True)

    monkeypatch.setattr(_session, "pareto_search", never)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert searched == [] and result["phase"] == "complete"
    assert cast(dict[str, object], result["search"])["reason"] == "converged-stalled"


@pytest.mark.ac("gh-240:AC-4")
def test_refused_proposals_count_as_no_gain_and_stop_the_search_after_eight(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.refuse = True
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    reflections = [item for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
                   if item.get("split") == "reflection"]
    assert state.proposals == len(reflections) == 8 and not any("proposed" in item for item in reflections)
    assert cast(dict[str, object], result["search"])["reason"] == "converged-stalled"
    assert result["phase"] == "complete"


@pytest.mark.ac("gh-240:AC-3")
def test_proposals_whose_evaluations_all_fail_never_stop_the_search(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    # The fault rule of gh-316 would stop this run first. Lift it, so that the test isolates the stall rule.
    monkeypatch.setattr(_session, "FAULT_MIN_FINISHED", 10**9)
    monkeypatch.setattr(_session, "FAULT_STREAK", 10**9)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    seed_calls = len(_validation(out))
    state.evaluate_fault = lambda number: RuntimeError("provider fell over") if number > seed_calls else None
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    # All feedback is unscored, so each reflection is skipped unpaid (gh-316:AC-1); no proposal runs and none counts toward a stall.
    skipped = [row for row in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]) if row.get("split") == "reflection"]
    assert state.proposals == 0 and state.prompts == []
    assert skipped and {row["reason"] for row in skipped} == {"no-scored-feedback"}
    assert cast(dict[str, object], result["search"])["reason"] == "validation-mean"


def test_a_spent_search_share_keeps_its_reason_over_a_recorded_stall(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = _stalls
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    record["phase"] = "prepared"
    filler: dict[str, object] = {"arm": "search", "split": "train", "case_id": "filler", "score": 0.0}
    cast(list[dict[str, object]], record["outcomes"]).extend([filler] * (_allowance(out) - _search_spent(out)))
    write(out / "run.json", record)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert cast(dict[str, object], result["search"])["reason"] == "search-share-spent-best-validated"


def _validated(identity: str, case: str, score: float) -> dict[str, object]:
    return {"arm": "search", "split": "validation", "case_id": case, "score": score, "search_candidate": identity}


def _reflection(proposed: str | None = None) -> dict[str, object]:
    outcome: dict[str, object] = {"arm": "search", "split": "reflection"}
    if proposed is not None:
        outcome["proposed"] = proposed
    return outcome


def test_a_failed_proposal_build_records_its_paid_reflection_and_stops_the_run(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    real = Candidate.changed

    def full_disk(self: Candidate, components: dict[str, str]) -> Candidate:
        if any("improved" in text for text in components.values()):
            raise OSError("no space left on device")
        return real(self, components)

    monkeypatch.setattr(Candidate, "changed", full_disk)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert caught.value.code == "proposal-build-failed"
    record = read(out / "run.json")
    reflections = [item for item in cast(list[dict[str, object]], record["outcomes"]) if item.get("split") == "reflection"]
    assert state.proposals == len(reflections) == 1 and "proposed" not in reflections[0]
    assert record["phase"] == "prepared" and record["failure_code"] == "proposal-build-failed"
    # After the host is fixed, the resumed run does not count the faulted proposal toward the stall streak.
    monkeypatch.setattr(Candidate, "changed", real)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete" and len(state.prompts) > 1
    assert _stall_counts([state.prompts[1].split("\n", 1)[0]]) == [0]


def _failed_train(identity: str) -> dict[str, object]:
    return {"arm": "search", "split": "train", "case_id": "t1", "score": 0.0, "search_candidate": identity,
            "status": "evaluation-failed"}


def test_each_proposal_observes_the_rise_of_its_own_candidate_over_every_earlier_candidate() -> None:
    outcomes = [_validated("seed", "v1", 0.5), _validated("seed", "v2", 0.5),
                # GEPA scores `a` again as the next parent; that failure comes after its validation set.
                _reflection("a"), _validated("a", "v1", 1.0), _validated("a", "v2", 0.5), _failed_train("a"),
                # 0.6 is above the seed but below the earlier best `a`.
                _reflection("e"), _validated("e", "v1", 0.7), _validated("e", "v2", 0.5),
                _reflection("b"), _validated("b", "v1", 0.5), _validated("b", "v2", 0.5),
                _validated("seed", "v1", 1.0), _validated("seed", "v2", 1.0),
                # 0.9 is above the previous candidate `b`, but the latest seed scores make the seed mean 1.0, so it is no gain.
                _reflection("f"), _validated("f", "v1", 0.9), _validated("f", "v2", 0.9),
                _reflection("c"), _failed_train("c"), _validated("c", "v1", 1.0), _validated("c", "v2", 1.0),
                _reflection(),
                _reflection("d"), _validated("d", "v1", 1.0)]
    assert _session.stall_observations(outcomes, {"v1", "v2"}) == [pytest.approx(0.25), 0.0, 0.0, 0.0, None, 0.0, 0.0]


def test_a_proposal_that_repeats_text_the_seed_already_holds_is_not_rejected(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals, seed_extra=SPAN)
    state.suffix = " " + SPAN
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert cast(dict[str, object], result["search"])["leakage_rejections"] == {}
    assert state.improved_calls > 0


@pytest.mark.ac("gh-247:AC-1")
def test_every_evaluation_in_a_session_gets_the_same_check_memo(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert len(state.memos) > 1
    assert isinstance(state.memos[0], CheckMemo)
    assert all(memo is state.memos[0] for memo in state.memos)


TOKEN = "zebra"


def _attempts(result: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], cast(dict[str, object], result["search"])["attempts"])


def _recorded_attempts(out: Path) -> list[dict[str, object]]:
    """Return the full attempts of run.json. The exported summary redacts the hypothesis and uncoded rejection text."""
    return cast(list[dict[str, object]], cast(dict[str, object], read(out / "run.json")["search"])["attempts"])


def _reflections_of(out: Path) -> list[dict[str, object]]:
    return [item for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"]) if item.get("split") == "reflection"]


@pytest.mark.ac("gh-241:AC-8")
def test_a_clean_proposal_after_the_history_is_shown_leaves_leakage_rejections_unchanged(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.hypothesis_of = lambda number: f"shorter wording {number}"
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert state.proposals >= 3 and state.improved_calls > 0
    assert "Attempt 1: hypothesis shorter wording 1;" in state.prompts[-1] and "No attempt yet." in state.prompts[0]
    search = cast(dict[str, object], result["search"])
    assert search["leakage_rejections"] == {} and search["text_dropped"] == {}


@pytest.mark.ac("gh-241:AC-10")
def test_the_search_record_holds_the_reason_the_counts_the_attempts_and_the_dropped_text_together(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    assert set(search) == {"reason", "retained_seed", "leakage_rejections", "proposal_over_budget", "attempts", "text_dropped",
                           "scored", "failed", "failed_by_reason"}
    saved = cast(dict[str, object], read(out / "run.json")["search"])
    assert set(saved) == set(search) and {key: saved[key] for key in saved if key != "attempts"} == {
        key: search[key] for key in search if key != "attempts"}
    attempts = _recorded_attempts(out)
    assert [item["index"] for item in _attempts(result)] == [item["index"] for item in attempts] == list(range(1, state.proposals + 1))
    assert attempts[0]["parent"] == read(out / "run.json")["seed_hash"] and attempts[0]["candidate"] is not None
    assert all(item["hypothesis"] is None and item["rejected"] is None for item in attempts)


@pytest.mark.ac("gh-241:AC-6")
def test_a_hypothesis_that_copies_a_request_span_is_dropped_and_the_proposal_is_still_admitted(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.hypothesis_of = lambda _number: f"try to {SPAN}"
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    assert state.proposals > 0 and state.improved_calls > 0
    assert search["leakage_rejections"] == {} and search["text_dropped"] == {"request-span": state.proposals}
    reflections = _reflections_of(out)
    assert all(item["hypothesis"] is None and item["hypothesis_dropped"] == "request-span" for item in reflections)
    assert all(item["proposed"] for item in reflections)
    assert SPAN not in (out / "run.json").read_text(encoding="utf-8")


@pytest.mark.ac("gh-241:AC-3")
def test_a_resumed_run_keeps_earlier_attempts_continues_the_index_and_keeps_the_dropped_count(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)

    def hypothesis(number: int) -> str:
        cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
        train = next(str(case["id"]) for case in cases if case["split"] == "train")
        return f"changed {train}" if number == 1 else f"idea {number}"

    state.hypothesis_of = hypothesis
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 3 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    before = _reflections_of(out)
    assert len(before) == 2 and before[0]["hypothesis_dropped"] == "case-id"
    result = run(target, out, MODEL, live=True, factory=state.factory)
    attempts = _recorded_attempts(out)
    assert len(attempts) > 2 and [item["index"] for item in attempts] == list(range(1, len(attempts) + 1))
    assert attempts[0]["hypothesis"] is None and attempts[0]["candidate"] == before[0]["proposed"]
    assert attempts[1]["hypothesis"] == "idea 2"
    assert cast(dict[str, object], result["search"])["text_dropped"] == {"case-id": 1}


@pytest.mark.ac("gh-241:AC-4")
def test_a_holdout_string_never_reaches_a_prompt_or_an_attempt(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=12)
    draft = cast(list[dict[str, object]], json.loads((out / "cases.draft.json").read_text(encoding="utf-8")))
    for case in draft:
        case["request"] = f"{case['request']} {TOKEN}-{case['id']}"
    _ = (out / "cases.draft.json").write_text(json.dumps(draft), encoding="utf-8")
    case_hash, calls = approvals(target, out)
    state = Search(target)
    state.hypothesis_of = lambda number: f"idea {number}"
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    held = [f"{TOKEN}-{case['id']}" for case in cases if case["split"] == "holdout"]
    seen = [f"{TOKEN}-{case['id']}" for case in cases if case["split"] == "train"]
    assert held and state.prompts
    text = "\n".join(state.prompts) + json.dumps(_attempts(result))
    assert not any(token in text for token in held)
    assert any(token in text for token in seen), "the train feedback still carries the train request"
    assert not any(token in json.dumps(_attempts(result)) for token in seen)


@pytest.mark.ac("gh-241:AC-9")
def test_a_shape_refusal_records_an_attempt_with_no_candidate_and_the_fixed_code(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.refuse = True
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 2 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    (stopped,) = _reflections_of(out)
    assert stopped["rejected"] == "reflection-shape"
    state.invoke_fault = lambda _number: None
    result = run(target, out, MODEL, live=True, factory=state.factory)
    attempts = _attempts(result)
    first = attempts[0]
    assert first["index"] == 1 and first["candidate"] is None and first["rejected"] == "reflection-shape"
    recorded = len(_reflections_of(out))
    assert recorded == 9 and [item["index"] for item in attempts] == list(range(1, recorded + 1))
    assert all(item["candidate"] is None and item["rejected"] == "reflection-shape" and item["accepted"] is False for item in attempts)
    assert all(item["rejected"] == "reflection-shape" and item["components_digest"] is None for item in _reflections_of(out))


@pytest.mark.ac("gh-241:AC-9")
def test_an_admit_rejection_records_an_attempt_with_no_candidate_and_its_reason(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " " + ANSWER
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 2 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    (stopped,) = _reflections_of(out)
    assert stopped["rejected"] == "candidate-leakage-rejected"
    state.invoke_fault = lambda _number: None
    result = run(target, out, MODEL, live=True, factory=state.factory)
    attempts = _attempts(result)
    first = attempts[0]
    assert first["index"] == 1 and first["candidate"] is None and first["rejected"] == "candidate-leakage-rejected"
    recorded = len(_reflections_of(out))
    assert recorded > 1 and [item["index"] for item in attempts] == list(range(1, recorded + 1))
    assert all(item["candidate"] is None and item["rejected"] == "candidate-leakage-rejected" for item in attempts)
    assert all(item["rejected"] == "candidate-leakage-rejected" for item in _reflections_of(out))


@pytest.mark.ac("gh-241:AC-9")
def test_a_proposal_build_failure_records_its_attempt_and_the_attempt_survives_the_resume(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    real = Candidate.changed

    def full_disk(self: Candidate, components: dict[str, str]) -> Candidate:
        if any("improved" in text for text in components.values()):
            raise OSError("no space left on device")
        return real(self, components)

    monkeypatch.setattr(Candidate, "changed", full_disk)
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    (stopped,) = _reflections_of(out)
    assert stopped["rejected"] == "proposal-build-failed" and stopped["status"] == "proposal-build-failed"
    monkeypatch.setattr(Candidate, "changed", real)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    first = _attempts(result)[0]
    assert first["index"] == 1 and first["candidate"] is None and first["rejected"] == "proposal-build-failed"
    assert len(_attempts(result)) > 1


@pytest.mark.ac("gh-241:AC-1")
def test_a_real_run_records_fixed_and_broke_inside_the_minibatch_and_a_later_parent_is_an_earlier_candidate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    attempts, reflections = _recorded_attempts(out), _reflections_of(out)
    train = {str(case["id"]) for case in cast(list[dict[str, object]], read(out / "cases.json")["cases"]) if case["split"] == "train"}
    listed = [(item, cast(list[str], reflection["minibatch"])) for item, reflection in zip(attempts, reflections)
              if item["fixed"] or item["broke"]]
    assert listed, "no attempt holds a fixed or broke id"
    for item, minibatch in listed:
        ids = cast(list[str], item["fixed"]) + cast(list[str], item["broke"])
        assert set(ids) <= set(minibatch) and set(ids) <= train
    assert any(item["parent"] in {earlier["candidate"] for earlier in attempts[:number]} for number, item in enumerate(attempts))
    rows = _attempts(result)
    assert len(rows) == len(attempts) and any(row["candidate"] is not None for row in rows)
    for row, full in zip(rows, attempts):
        assert set(row) == {"index", "parent", "candidate", "fixed", "broke", "validation_delta", "accepted", "rejected"}
        mapped = None if full["rejected"] is None else full["rejected"] if full["rejected_coded"] else "rejected"
        assert row == {key: full[key] for key in row if key != "rejected"} | {"rejected": mapped}


def test_the_protect_set_follows_the_seed_when_a_validated_candidate_is_worse_than_the_seed(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    real = Provider.evaluate

    def evaluate(self: Provider, candidate: Candidate, case: Case, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        result = real(self, candidate, case, holdout=holdout, checks=checks)
        if holdout:
            return result
        improved, even = "improved" in candidate.files["SKILL.md"], sum(map(ord, case.identifier)) % 2 == 0
        if case.split == "train":
            result["score"] = 1.0 if even != improved else 0.5
        elif case.split == "validation":
            result["score"] = 0.5 if improved else 1.0
        return result

    monkeypatch.setattr(Provider, "evaluate", evaluate)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    marker = "Keep these train cases passing, because the current best passes them: "
    kept = [prompt.split("\n", 1)[0] for prompt in state.prompts if marker in prompt.split("\n", 1)[0]]
    assert len(kept) >= 2
    for head in kept:
        names = head.split(marker, 1)[1].split(".", 1)[0].split(", ")
        assert names and all(sum(map(ord, name)) % 2 == 0 for name in names)


def test_the_export_summary_redacts_the_hypothesis_and_uncoded_rejection_text_but_run_json_keeps_them(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.hypothesis_of = lambda number: f"hypothesis-sentinel {number}"
    real = Candidate.changed

    def failing(self: Candidate, components: dict[str, str]) -> Candidate:
        if any("improved" in text for text in components.values()):
            raise ValueError("uncoded-sentinel text")
        return real(self, components)

    monkeypatch.setattr(Candidate, "changed", failing)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    saved = (out / "run.json").read_text(encoding="utf-8")
    assert "hypothesis-sentinel" in saved and "uncoded-sentinel text" in saved
    report = (Path(cast(str, export(out, tmp_path / "export")["export"])) / "report.json").read_text(encoding="utf-8")
    assert "hypothesis-sentinel" not in report and "uncoded-sentinel" not in report
    assert "hypothesis-sentinel" not in json.dumps(result) and "uncoded-sentinel" not in json.dumps(result)
    rows = _attempts(result)
    assert rows and all("hypothesis" not in row and row["rejected"] == "rejected" for row in rows)
    recorded = _recorded_attempts(out)
    assert len(rows) == len(recorded)
    for row, full in zip(rows, recorded):
        assert set(row) == {"index", "parent", "candidate", "fixed", "broke", "validation_delta", "accepted", "rejected"}
        assert row == {key: full[key] for key in row if key != "rejected"} | {"rejected": "rejected"}


def test_the_export_keeps_a_coded_rejection_and_maps_a_one_word_uncoded_text_to_rejected(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    real = Candidate.changed
    seen: list[int] = []

    def failing(self: Candidate, components: dict[str, str]) -> Candidate:
        if any("improved" in text for text in components.values()):
            seen.append(1)
            raise CodedError("hidden-file", "coded text") if len(seen) % 2 else ValueError("sentinelword")
        return real(self, components)

    monkeypatch.setattr(Candidate, "changed", failing)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert "sentinelword" in (out / "run.json").read_text(encoding="utf-8")
    report = (Path(cast(str, export(out, tmp_path / "export")["export"])) / "report.json").read_text(encoding="utf-8")
    assert "sentinelword" not in report and "sentinelword" not in json.dumps(result)
    assert {row["rejected"] for row in _attempts(result)} == {"hidden-file", "rejected"}


def test_a_spent_call_whose_answer_has_no_hypothesis_key_records_a_null_hypothesis_and_an_attempt(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.omit_hypothesis = True
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    reflections, attempts = _reflections_of(out), _recorded_attempts(out)
    assert reflections and len(attempts) == len(reflections)
    assert all(item["hypothesis"] is None and item["hypothesis_dropped"] is None for item in reflections)
    assert all(item["hypothesis"] is None for item in attempts) and any(item["accepted"] for item in attempts)
