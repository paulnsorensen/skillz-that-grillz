"""The search share of a run: GEPA headroom, best validated pick, failed evaluations, and resume."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments.evaluation import CheckMemo, CheckResult
from skillz_experiments.harness import Configuration, EnvironmentDiffers, Harness
from skillz_experiments.harness._codex import Codex
from skillz_experiments._intake import intake_contract
from skillz_experiments._records import read
from skillz_experiments._records import write
from skillz_experiments._budget import MAX_CONCURRENT_CALLS, Budget, BudgetExhausted
from skillz_experiments.search import overshoot
from skillz_experiments.run import _outcomes, _session
from skillz_experiments.run import Stop, export, run
from skillz_experiments.run._plan import PREFLIGHT_CALLS, VALIDATION_REPEATS
from typing_extensions import override
from skillz_experiments.search._history import DIFFERENT, EARLIER_INTRO, NOTES_INTRO, REJECTED_INTRO
from skillz_experiments.run._history_store import UNREADABLE, FileHistoryStore, store_path
from skillz_experiments.run._ports import Ports
from fake_provider import PASSED, FakeRunProvider, ProviderCrash

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
        self.split_fault: Callable[[str], Exception | None] = lambda _split: None
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
        self.notes_of: Callable[[int], str | None] = lambda _number: None
        self.omit_hypothesis = False
        # When set, a proposal changes only this component and returns the parent text of every other one.
        self.only: str | None = None

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> Provider:
        return Provider(self, budget, checkpoint)


@final
class Provider(FakeRunProvider):
    def __init__(self, state: Search, budget: Budget, checkpoint: Callable[[], None]) -> None:
        super().__init__()
        self.state = state
        self.budget = budget
        self.checkpoint = checkpoint

    @override
    def preflight(self, recorded: dict[str, object] | None = None) -> dict[str, object]:
        """Claim the live calls once. A resume reuses the recorded pass, as the real harness does."""
        del recorded
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
            fault = state.evaluate_fault(number) or state.split_fault(case.split)
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
                               for name, text in parent.items()} | self._free_text(number)}
        parent_texts = cast(dict[str, dict[str, str]], json.loads(prompt.split("\n", 1)[1]))["candidate"]
        texts = {name: f"improved {self.state.fixed_number or number} " + "x" * self.state.padding + self.state.suffix
                 if self.state.only in (None, name) else parent_texts[name]
                 for name in cast(list[str], schema["required"]) if name not in ("@hypothesis", "@notes")}
        if self.state.omit_hypothesis:
            return {"answer": texts}
        return {"answer": texts | self._free_text(number)}

    def _free_text(self, number: int) -> dict[str, str | None]:
        return {"@hypothesis": self.state.hypothesis_of(number), "@notes": self.state.notes_of(number)}


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
    state.exhaust_after_improved = _validation_runs(out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert _core(result) == {
        "reason": "budget-exhausted-best-validated", "retained_seed": False, "leakage_rejections": {}, "proposal_over_budget": 0}
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]

def _core(result: dict[str, object]) -> dict[str, object]:
    """Return the search fields that the attempt record and the evaluation counts did not add. `test_search_faults.py` covers the counts."""
    search = cast(dict[str, object], result["search"])
    # A search that compared a challenger with the incumbent also holds `comparisons` (gh-500).
    assert set(search) - {"scored", "failed", "failed_by_reason", "comparisons"} == {"reason", "stop_trigger", "retained_seed", "leakage_rejections", "proposal_over_budget", "attempts", "text_dropped", "history"}
    return {key: search[key] for key in ("reason", "retained_seed", "leakage_rejections", "proposal_over_budget")}



def _budgets(prompts: list[str]) -> list[int]:
    return [int(prompt.split("Make at most ", 1)[1].split(" ", 1)[0]) for prompt in prompts]


LAST_PROPOSAL = "This may be the last proposal, so prefer one small, safe change."


def _seed_pass(out: Path) -> int:
    """Return the evaluations of the headroom pass: one for each train case and one per repeat of each validation case."""
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    return sum({"train": 1, "validation": VALIDATION_REPEATS}.get(cast(str, case["split"]), 0) for case in cases)


def _headroom(out: Path) -> int:
    """Return the headroom outcomes that the plan adds to the search ceiling."""
    estimate = cast(dict[str, object], read(out / "run.json")["estimate"])
    return cast(dict[str, int], estimate["headroom"])["outcomes"]


def _allowance(out: Path) -> int:
    """Return the GEPA call budget: the planned calls and the headroom outcomes, less the overshoot margin.

    The last iteration can pass it by up to that margin.
    """
    planned = cast(int, cast(dict[str, object], read(out / "run.json")["estimate"])["metric_calls"])
    return planned + _headroom(out) - overshoot(_validation_runs(out))


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
    # Each proposal beats the one before it, so the stall stop never ends the run before the search share does.
    state.score_of = lambda text, _split: min(0.9, 0.3 + 0.01 * int(text.split()[1])) if text.startswith("improved") else None
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
    state.abort_after_improved = _validation_runs(out)
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


def _validation_runs(out: Path) -> int:
    """Return the validation runs of one candidate: each validation case once per repeat."""
    return VALIDATION_REPEATS * len(_validation(out))


def test_one_failed_evaluation_scores_zero_and_does_not_abort_the_search(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    fault_at = _seed_pass(out) + 3
    state.evaluate_fault = lambda number: RuntimeError("provider fell over") if number == fault_at else None
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
    fault_at = _seed_pass(out) + 3
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == fault_at else None)
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert record["phase"] == "prepared"
    spent = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation")
                for item in cast(list[dict[str, object]], record["outcomes"]))
    assert _seed_pass(out) < spent < _allowance(out)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    # GEPA counts each reused outcome as a metric call, so the grant adds the `spent` reused outcomes back.
    assert allowances == [_allowance(out), _allowance(out)]


def _search_pairs(record: dict[str, object]) -> list[tuple[str, str, str]]:
    """Return the (candidate identity, split, case) of each recorded search evaluation that scored."""
    return [(cast(str, item["search_candidate"]), cast(str, item["split"]), cast(str, item["case_id"]))
            for item in cast(list[dict[str, object]], record["outcomes"])
            if item.get("arm") == "search" and item.get("split") in ("train", "validation")
            and "search_candidate" in item and item.get("status") != "evaluation-failed"]


def _interrupt(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
               approvals: Callable[..., tuple[str, int]]) -> tuple[Path, Path, Search, int]:
    """Stop a run on its third call inside the search, after the headroom pass.

    Return the target, the run directory, the provider state, and the planned search evaluations: the metric calls and the headroom outcomes.
    """
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    fault_at = _seed_pass(out) + 3
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == fault_at else None)
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    estimate = cast(dict[str, object], read(out / "run.json")["estimate"])
    return target, out, state, cast(int, estimate["metric_calls"]) + _headroom(out)


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
    margin = overshoot(_validation_runs(out))
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


@pytest.mark.ac("gh-500:AC-1")
def test_one_search_scores_each_train_pair_once_and_each_validation_pair_once_per_repeat(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """GEPA scores the parent again on each minibatch. The session serves the repeat from the recorded outcome."""
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    expected = {"train": 1, "validation": VALIDATION_REPEATS}
    assert {pair: count for pair, count in Counter(state.search_log).items() if count != expected[pair[1]]} == {}
    assert Counter(_search_pairs(read(out / "run.json"))) == Counter(state.search_log)
    outcomes = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    repeats: dict[tuple[str, str], list[object]] = {}
    for item in outcomes:
        if item.get("arm") == "search" and item.get("split") == "validation":
            repeats.setdefault((cast(str, item["search_candidate"]), cast(str, item["case_id"])), []).append(item.get("repeat"))
    # Each (candidate, case) holds its own outcome for repeat 0 and repeat 1, not one outcome served twice.
    assert repeats and all(sorted(cast(list[int], found)) == [0, 1] for found in repeats.values())

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
    fault_at = state.search_calls + 3
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == fault_at else None)
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
    state.abort_after_improved = _validation_runs(out)
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
    state.abort_after_improved = _validation_runs(out)
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    planned = cast(int, cast(dict[str, object], record["estimate"])["metric_calls"]) + _headroom(out)
    # A live model proposes new candidates on resume, so the outcomes of an old candidate are never asked for again.
    # The ghost count stays the pre-gh-500 size, which leaves the resumed search some allowance.
    ghosts = 3 * overshoot(len(_validation(out)))
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
    observations = _outcomes.stall_observations(cast(list[dict[str, object]], record["outcomes"]), set(_validation(out)),
                                                VALIDATION_REPEATS)
    # The first window ends at the interrupt. The second window holds the same proposal, which now completes its validation.
    assert len(observations) >= 2 and observations[1] is not None and observations[1] > 0


def test_a_resume_with_a_spent_search_share_keeps_the_candidate_validated_before_the_abort(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    validation = _validation(out)
    state.abort_after_improved = VALIDATION_REPEATS * len(validation)
    with pytest.raises(CodedError, match="the process stopped"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    spent = sum(item.get("arm") == "search" and item.get("split") in ("train", "validation") for item in outcomes)
    filler: dict[str, object] = {"arm": "search", "split": "train", "case_id": "filler", "score": 0.0}
    outcomes.extend([filler] * (_allowance(out) - spent))
    write(out / "run.json", record)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert _core(result) == {"reason": "search-share-spent-best-validated", "retained_seed": False, "leakage_rejections": {}, "proposal_over_budget": 0}
    assert "improved" in cast(dict[str, str], read(out / "run.json")["winner"])["SKILL.md"]


def test_a_resume_with_a_real_allowance_keeps_the_pre_abort_candidate_over_worse_later_proposals(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.abort_after_improved = _validation_runs(out)
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
    # The seed stays the incumbent: each comparison rejects the challenger, so no `best_validated` is written.
    comparisons = cast(list[dict[str, object]], cast(dict[str, object], record["search"])["comparisons"])
    assert "best_validated" not in record and comparisons and {item["verdict"] for item in comparisons} == {"reject"}
    assert _core(result) == {"reason": "validation-mean", "retained_seed": True, "leakage_rejections": {}, "proposal_over_budget": 0}
    assert "reason" not in cast(dict[str, object], record["gate"])


def test_a_validated_candidate_that_ties_the_seed_leaves_the_seed_as_the_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: (0.5 if split == "validation" else 0.9) if "improved" in text else 0.5
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    # A tie is inside the 2-SE band, so the seed stays the incumbent.
    comparisons = cast(list[dict[str, object]], cast(dict[str, object], record["search"])["comparisons"])
    assert "best_validated" not in record and comparisons
    assert {(item["verdict"], item["delta"]) for item in comparisons} == {("inconclusive", 0.0)}
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
        return real(*args, **{**kwargs, "metric_calls": VALIDATION_REPEATS * len(cast(list[object], args[3])) + 2})  # pyright: ignore[reportAny]

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
    assert "candidates" not in record and set(kept) == {"identity", "mean", "files", "comparison"}
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
    fault_at = _seed_pass(out) + 3
    state.evaluate_fault = lambda number: CodedError("other-code", "a coded stop") if number == fault_at else None
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

    def check_candidate(self, candidate: Candidate) -> CheckResult:
        self.seen.append(candidate)
        return CheckResult(False, "fixture", "fail")


def test_harness_check_candidate_delegates_to_the_task_transport(
        tmp_path: Path, make_target: Callable[..., Path]) -> None:
    seed = Candidate.capture(make_target(tmp_path), [], intake_contract(make_target(tmp_path / "other")))
    task = _Task()
    harness = object.__new__(Harness)
    harness.configuration = cast(object, _Identity())  # pyright: ignore[reportAttributeAccessIssue]
    harness.identity = {}
    harness.transports = cast(dict[str, object], {"task": task})  # pyright: ignore[reportAttributeAccessIssue]
    assert harness.check_candidate(seed) == CheckResult(False, "fixture", "fail")
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

    def accept(self: Codex, candidate: Candidate) -> CheckResult:
        del self, candidate
        return PASSED

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
                seed_extra: str = "", request_tail: str = "", harness_phrases: tuple[str, ...] = (),
                ) -> tuple[Path, Path, Search, str, int]:
    """Start a run whose first case holds a long request, a long expected answer, and a named fixture.

    `request_tail` ends every request, and `harness_phrases` go into the contract.
    """
    target, out = make_target(tmp_path), tmp_path / "run"
    if harness_phrases:
        contract = target / "evals/autoimprove.json"
        _ = contract.write_text(json.dumps(json.loads(contract.read_text()) | {"harness_phrases": list(harness_phrases)}))
    if seed_extra:
        with (target / "SKILL.md").open("a", encoding="utf-8") as handle:
            _ = handle.write("\n" + seed_extra + "\n")
    cases: list[dict[str, object]] = []
    for family in range(5):
        for index in range(2):
            name = f"t{family}-{index}"
            cases.append({"id": name, "family": f"f{family}", "kind": "task", "request": f"please {SPAN} payment {name} today{request_tail}",
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


@pytest.mark.ac("gh-302:AC-10")
def test_a_proposal_that_adds_a_runner_term_to_a_wedge_free_target_is_rejected(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " No buildable wedge.toml accompanies the supplied skill components."
    seen = _spy_feedback(monkeypatch)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    editable = cast(list[str], read(out / "run.json")["editable"])
    assert not any(name == "@new-cli" or name.startswith("@wedge/") for name in editable)
    rejections = cast(dict[str, int], cast(dict[str, object], result["search"])["leakage_rejections"])
    assert state.proposals > 0 and state.improved_calls == 0
    assert list(rejections) == ["runner-term"] and 0 < rejections["runner-term"] <= state.proposals
    assert any(feedback.get("rule") == "runner-term" and score == 0.0 for score, feedback in seen)


@pytest.mark.ac("gh-499:AC-5")
@pytest.mark.parametrize("suffix", [" Write the plan to output/plan.json.", " Do not run git or gh."])
def test_a_proposal_that_adds_harness_text_is_rejected_and_counted_under_harness_token(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals, request_tail=" in output/plan.json",
                                                       harness_phrases=("do not run git or gh",))
    state.suffix = suffix
    seen = _spy_feedback(monkeypatch)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    rejections = cast(dict[str, int], cast(dict[str, object], result["search"])["leakage_rejections"])
    assert state.proposals > 0 and state.improved_calls == 0
    assert list(rejections) == ["harness-token"] and 0 < rejections["harness-token"] <= state.proposals
    assert any(feedback.get("rule") == "harness-token" and score == 0.0 for score, feedback in seen)
    assert cast(dict[str, object], read(out / "run.json")["search"])["leakage_rejections"] == rejections


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
@pytest.mark.ac("gh-500:AC-7")
def test_a_search_that_stalls_for_eight_proposals_stops_converged_stalled_and_still_runs_the_gate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = _stalls
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert state.proposals == 9 and _search_spent(out) < _allowance(out)
    assert cast(dict[str, object], result["search"])["reason"] == "converged-stalled"
    assert cast(dict[str, object], read(out / "run.json")["search"])["stop_trigger"] == "stall"
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
    # SKILL.md is the only editable component, so the focus is no choice and the generic `shift` advice stays (W4.7).
    assert [SHIFT in head for head in heads] == [False] * 6 + [True] * 3
    assert not any("because earlier attempts changed it least" in head for head in heads)
    assert not any("focus" in item for item in _reflections_of(out))


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
    monkeypatch.setattr(_outcomes, "FAULT_MIN_FINISHED", 10**9)
    monkeypatch.setattr(_outcomes, "FAULT_STREAK", 10**9)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    seed_calls = _seed_pass(out)
    # The headroom pass scores the seed on the validation cases only. Its train cases fail, so the feedback stays unscored.
    state.split_fault = lambda split: RuntimeError("provider fell over") if split != "validation" else None
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
    assert _outcomes.stall_observations(outcomes, {"v1", "v2"}) == [pytest.approx(0.25), 0.0, 0.0, 0.0, None, 0.0, 0.0]


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
    assert set(search) == {"reason", "stop_trigger", "retained_seed", "leakage_rejections", "proposal_over_budget", "attempts",
                           "text_dropped", "scored", "failed", "failed_by_reason", "history", "comparisons"}
    saved = cast(dict[str, object], read(out / "run.json")["search"])
    # The exported summary drops the proposer journal; run.json keeps it.
    assert set(saved) == set(search) | {"journal"} and {key: saved[key] for key in search if key != "attempts"} == {
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


@pytest.mark.usefixtures("headroom_off")
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


def _head(prompt: str) -> str:
    return prompt.split("\n", 1)[0]


@pytest.mark.ac("gh-242:AC-1")
def test_a_leakage_rejected_attempt_shows_its_hypothesis_and_reason_in_the_next_prompt_with_the_different_instruction(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " " + ANSWER
    state.hypothesis_of = lambda number: f"idea {number}"
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert len(state.prompts) >= 2 and REJECTED_INTRO not in _head(state.prompts[0])
    second = _head(state.prompts[1])
    assert REJECTED_INTRO + "Attempt 1: hypothesis idea 1; reason candidate-leakage-rejected. " + DIFFERENT in second
    newest = _head(state.prompts[-1])
    shown = newest.split(REJECTED_INTRO, 1)[1].split(DIFFERENT, 1)[0]
    assert shown.count("; reason candidate-leakage-rejected.") == min(6, len(state.prompts) - 1)
    assert shown.startswith(f"Attempt {len(state.prompts) - 1}: hypothesis idea {len(state.prompts) - 1};")


@pytest.mark.ac("gh-242:AC-1")
def test_a_fully_scored_proposal_without_a_validation_gain_is_shown_as_no_gain(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, split: 0.7 if "improved" in text and split == "train" else 0.2
    state.hypothesis_of = lambda number: f"idea {number}"
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    flat = [item for item in _recorded_attempts(out) if item["validation_delta"] is not None]
    assert flat and all(item["validation_delta"] == 0.0 and item["accepted"] for item in flat)
    first = cast(int, flat[0]["index"])
    assert len(state.prompts) > first
    assert f"Attempt {first}: hypothesis idea {first}; reason no-gain." in _head(state.prompts[first])


@pytest.mark.ac("gh-242:AC-3")
def test_proposer_notes_join_the_journal_and_the_next_prompt_newest_first_and_stay_out_of_the_export(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.notes_of = lambda number: f"note-sentinel {number}"
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    saved = cast(dict[str, object], read(out / "run.json")["search"])
    assert state.proposals >= 3
    assert saved["journal"] == [{"index": number, "notes": f"note-sentinel {number}"} for number in range(1, state.proposals + 1)]
    assert NOTES_INTRO not in _head(state.prompts[0])
    assert NOTES_INTRO + "Attempt 1: note-sentinel 1" in _head(state.prompts[1])
    assert NOTES_INTRO + "Attempt 2: note-sentinel 2 | Attempt 1: note-sentinel 1" in _head(state.prompts[2])
    history, notes = _head(state.prompts[2]).index("Attempt history, newest first."), _head(state.prompts[2]).index(NOTES_INTRO)
    assert history < notes, "the framework facts come before the proposer notes, in their own section"
    assert "journal" not in cast(dict[str, object], result["search"]) and "note-sentinel" not in json.dumps(result)
    report = (Path(cast(str, export(out, tmp_path / "export")["export"])) / "report.json").read_text(encoding="utf-8")
    assert "note-sentinel" not in report


@pytest.mark.ac("gh-242:AC-4")
def test_a_resumed_run_keeps_the_journal_and_the_rejected_memory(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " " + ANSWER
    state.hypothesis_of = lambda number: f"idea {number}"
    state.notes_of = lambda number: f"note {number}"
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 3 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert len(_reflections_of(out)) == 2 and len(state.prompts) == 3
    state.invoke_fault = lambda _number: None
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    resumed = _head(state.prompts[3])
    assert (REJECTED_INTRO + "Attempt 2: hypothesis idea 2; reason candidate-leakage-rejected. "
            + "Attempt 1: hypothesis idea 1; reason candidate-leakage-rejected. " + DIFFERENT) in resumed
    assert NOTES_INTRO + "Attempt 2: note 2 | Attempt 1: note 1" in resumed
    journal = cast(list[dict[str, object]], cast(dict[str, object], read(out / "run.json")["search"])["journal"])
    assert journal[:2] == [{"index": 1, "notes": "note 1"}, {"index": 2, "notes": "note 2"}]
    assert [item["index"] for item in journal] == list(range(1, len(_reflections_of(out)) + 1))


@pytest.mark.ac("gh-242:AC-5")
def test_a_note_that_copies_a_request_span_is_dropped_and_counted_in_text_dropped(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.notes_of = lambda _number: f"remember to {SPAN}"
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    search = cast(dict[str, object], result["search"])
    assert state.proposals > 0 and state.improved_calls > 0
    assert search["leakage_rejections"] == {} and search["text_dropped"] == {"request-span": state.proposals}
    reflections = _reflections_of(out)
    assert all(item["notes"] is None and item["notes_dropped"] == "request-span" and item["proposed"] for item in reflections)
    assert cast(dict[str, object], read(out / "run.json")["search"])["journal"] == []
    assert SPAN not in (out / "run.json").read_text(encoding="utf-8")
    assert not any(NOTES_INTRO in _head(prompt) for prompt in state.prompts)


def _again(tmp_path: Path, target: Path, first: Path, approvals: Callable[..., tuple[str, int]], name: str = "second",
           statistics: dict[str, float] | None = None) -> tuple[Path, Search, str, int]:
    """Prepare another run of `target` with the case draft of `first`, so its fingerprint matches unless `statistics` differ."""
    out = tmp_path / name
    out.mkdir(mode=0o700)
    _ = (out / "cases.draft.json").write_text((first / "cases.draft.json").read_text(encoding="utf-8"), encoding="utf-8")
    case_hash, calls = approvals(target, out, statistics=statistics)
    return out, Search(target), case_hash, calls


def _store(out: Path) -> Path:
    record = read(out / "run.json")
    return store_path(cast(str, cast(dict[str, object], record["contract"])["skill"]), cast(str, record["target_root"]))


def _history(result: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], cast(dict[str, object], result["search"])["history"])


def _earlier(prompt: str) -> str:
    head = _head(prompt)
    return head.split(EARLIER_INTRO, 1)[1].split(" Make at most", 1)[0] if EARLIER_INTRO in head else ""


@pytest.mark.ac("gh-243:AC-1")
def test_a_second_compatible_run_loads_the_first_run_from_the_store_and_shows_a_bounded_summary_without_case_text(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, first, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.hypothesis_of = lambda number: f"first-run idea {number}"
    _ = run(target, first, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    first_id, tried = read(first / "run.json")["run_id"], len(_recorded_attempts(first))
    assert not any(EARLIER_INTRO in _head(prompt) for prompt in state.prompts) and tried >= 3
    second, again, case_hash, calls = _again(tmp_path, target, first, approvals)
    result = run(target, second, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=again.factory)
    assert _history(result) == {"enabled": True, "runs": [first_id], "excluded": [], "skipped_lines": 0, "warnings": []}
    section = _earlier(again.prompts[0])
    assert section.startswith(f"Run 1 attempt {tried}: hypothesis first-run idea {tried};")
    assert section.count("Run 1 attempt") == min(8, tried) and len(EARLIER_INTRO + section) <= 4000
    assert "request-" not in section, "the summary holds no case text"
    lines = [cast(dict[str, object], json.loads(line)) for line in _store(first).read_text(encoding="utf-8").splitlines()]
    assert [line["run_id"] for line in lines] == [first_id, read(second / "run.json")["run_id"]]
    assert "request-" not in _store(first).read_text(encoding="utf-8")


@pytest.mark.ac("gh-243:AC-2")
def test_a_run_with_a_differing_compatibility_field_excludes_the_earlier_run_by_name(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, first, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " " + ANSWER
    first_result = run(target, first, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert cast(dict[str, object], first_result["search"])["reason"] == "converged-stalled"
    gains = {"min_gain": 0.01}
    second, again, case_hash, calls = _again(tmp_path, target, first, approvals, statistics=gains)
    result = run(target, second, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=again.factory,
                 statistics=gains)
    assert _history(result)["excluded"] == [{"run_id": read(first / "run.json")["run_id"], "fields": ["statistics"]}]
    assert _history(result)["runs"] == [] and not any(_earlier(prompt) for prompt in again.prompts)
    assert "Proposals since the last validation gain: 0." in _head(again.prompts[0]) and again.proposals > 1


@pytest.mark.ac("gh-243:AC-3")
def test_an_earlier_rejected_attempt_shows_in_history_and_never_becomes_the_winner_or_feeds_the_stall(
        tmp_path: Path, make_target: Callable[..., Path], approvals: Callable[..., tuple[str, int]]) -> None:
    target, first, state, case_hash, calls = _leak_start(tmp_path, make_target, approvals)
    state.suffix = " " + ANSWER
    state.hypothesis_of = lambda number: f"leaky idea {number}"
    _ = run(target, first, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    second, again, case_hash, calls = _again(tmp_path, target, first, approvals)
    result = run(target, second, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=again.factory)
    section = _earlier(again.prompts[0])
    assert ("Run 1 attempt 1: hypothesis leaky idea 1; fixed none; broke none; validation change unknown; "
            + "result rejected, candidate-leakage-rejected.") in section
    assert "Proposals since the last validation gain: 0." in _head(again.prompts[0]) and again.proposals > 1
    record = read(second / "run.json")
    own = {item.get("search_candidate") for item in cast(list[dict[str, object]], record["outcomes"])}
    assert record["winner_hash"] in own and ANSWER not in json.dumps(record["winner"])
    assert cast(dict[str, object], result["search"])["retained_seed"] is False


@pytest.mark.ac("gh-243:AC-4")
def test_a_holdout_secret_never_reaches_the_store_or_a_later_prompt(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, first = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(first, families=12)
    draft = cast(list[dict[str, object]], json.loads((first / "cases.draft.json").read_text(encoding="utf-8")))
    for case in draft:
        case["request"] = f"{case['request']} {TOKEN}-{case['id']}"
    _ = (first / "cases.draft.json").write_text(json.dumps(draft), encoding="utf-8")
    case_hash, calls = approvals(target, first)
    state = Search(target)
    state.hypothesis_of = lambda number: f"idea {number}"
    _ = run(target, first, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    second, again, case_hash, calls = _again(tmp_path, target, first, approvals)
    _ = run(target, second, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=again.factory)
    cases = cast(list[dict[str, object]], read(first / "cases.json")["cases"])
    held = [f"{TOKEN}-{case['id']}" for case in cases if case["split"] == "holdout"]
    stored = _store(first).read_text(encoding="utf-8")
    assert held and _earlier(again.prompts[0])
    assert TOKEN not in stored, "the store holds no request text at all"
    assert not any(token in prompt for token in held for prompt in again.prompts)


@pytest.mark.ac("gh-243:AC-5")
@pytest.mark.parametrize("broken", ["corrupt-line", "unreadable"])
def test_a_corrupt_or_unreadable_store_keeps_the_run_result_and_reports_it(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], broken: str) -> None:
    target, first, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _ = run(target, first, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    store = _store(first)
    if broken == "corrupt-line":
        with store.open("a", encoding="utf-8") as handle:
            _ = handle.write("{not json\n")
    else:
        store.chmod(0o644)
    control, plain, case_hash, calls = _again(tmp_path, target, first, approvals, name="control")
    expected = run(target, control, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=plain.factory,
                   history=False)
    second, again, case_hash, calls = _again(tmp_path, target, first, approvals)
    result = run(target, second, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=again.factory)
    history = _history(result)
    if broken == "corrupt-line":
        assert history["skipped_lines"] == 1 and history["warnings"] == [] and history["runs"] == [read(first / "run.json")["run_id"]]
    else:
        assert history["warnings"] == [UNREADABLE] and history["runs"] == [] and not _earlier(again.prompts[0])
        assert str(store) not in json.dumps(result)
    assert _core(result) == _core(expected) and read(second / "run.json")["winner"] == read(control / "run.json")["winner"]
    assert result["gate"] == expected["gate"]


@pytest.mark.ac("gh-243:AC-6")
def test_no_history_neither_reads_nor_writes_the_store(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], history_state: Path) -> None:
    target, first, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    result = run(target, first, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
                 history=False)
    assert _history(result) == {"enabled": False} and read(first / "run.json")["history"] is False
    assert not any(history_state.iterdir()), "a run with --no-history creates no store"
    second, again, case_hash, calls = _again(tmp_path, target, first, approvals)
    _ = run(target, second, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=again.factory)
    before = _store(first).read_bytes()
    third, quiet, case_hash, calls = _again(tmp_path, target, first, approvals, name="third")
    _ = run(target, third, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=quiet.factory,
            history=False)
    assert _store(first).read_bytes() == before and not any(_earlier(prompt) for prompt in quiet.prompts)


@pytest.mark.ac("gh-243:AC-6")
def test_a_resume_that_changes_the_history_flag_stops_with_run_config_differs(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.invoke_fault = lambda number: CodedError("harness-changed", "a guard fired") if number == 2 else None
    with pytest.raises(CodedError):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
                history=False)
    state.invoke_fault = lambda _number: None
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, live=True, factory=state.factory, history=True)
    assert caught.value.code == "run-config-differs" and "--history" in str(caught.value)
    assert "--no-history" not in str(caught.value), "the message names the flag the user passed"
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert _history(result) == {"enabled": False}


FOCUS = "Change references/alpha.md, because earlier attempts changed it least."


def _focus_start(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
                 approvals: Callable[..., tuple[str, int]], tag: str = "") -> tuple[Path, Path, Search, str, int]:
    """Start a run with three editable Markdown files. The provider changes only SKILL.md, and the search stalls."""
    target, out = make_target(tmp_path), tmp_path / "run"
    (target / "references").mkdir()
    for name in ("alpha", "beta"):
        _ = (target / f"references/{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    _ = write_draft(out, families=12)
    if tag:
        draft = cast(list[dict[str, object]], json.loads((out / "cases.draft.json").read_text(encoding="utf-8")))
        for case in draft:
            case["request"] = f"{case['request']} {tag}-{case['id']}"
        _ = (out / "cases.draft.json").write_text(json.dumps(draft), encoding="utf-8")
    case_hash, calls = approvals(target, out)
    state = Search(target)
    state.only = "SKILL.md"
    state.score_of = _stalls
    return target, out, state, case_hash, calls


@pytest.mark.ac("gh-244:AC-1")
def test_a_shift_names_the_least_tested_editable_component_with_the_lexical_tie_break_and_records_it(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _focus_start(tmp_path, make_target, write_draft, approvals)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert read(out / "run.json")["editable"] == ["SKILL.md", "references/alpha.md", "references/beta.md"]
    heads = [_head(prompt) for prompt in state.prompts]
    counts = _stall_counts(heads)
    shifted = [5 <= count < 8 for count in counts]
    assert counts == [0, 0, 1, 2, 3, 4, 5, 6, 7] and sum(shifted) == 3
    for head, shift in zip(heads, shifted):
        assert (FOCUS in head) is shift and SHIFT not in head, "the focus sentence replaces the generic shift advice"
    reflections = _reflections_of(out)
    assert all(item["changed"] == ["SKILL.md"] for item in reflections)
    for item, shift in zip(reflections, shifted):
        assert (item.get("focus"), item.get("focus_reason")) == (("references/alpha.md", "under-tested") if shift else (None, None))
    attempts = _recorded_attempts(out)
    assert [item.get("focus") for item in attempts] == [item.get("focus") for item in reflections]


@pytest.mark.ac("gh-244:AC-3")
def test_a_real_gain_resets_the_streak_and_ends_the_focus(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _focus_start(tmp_path, make_target, write_draft, approvals)
    state.score_of = lambda text, split: 0.95 if "improved 7 " in text else _stalls(text, split)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    heads = [_head(prompt) for prompt in state.prompts]
    counts = _stall_counts(heads)
    assert counts[:8] == [0, 0, 1, 2, 3, 4, 5, 0]
    assert FOCUS in heads[6] and FOCUS not in heads[7]
    reflections = _reflections_of(out)
    assert reflections[6].get("focus") == "references/alpha.md" and "focus" not in reflections[7]


@pytest.mark.ac("gh-244:AC-4")
def test_a_holdout_secret_stays_out_of_the_focus_choice_and_its_reason(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _focus_start(tmp_path, make_target, write_draft, approvals, tag=TOKEN)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    held = [f"{TOKEN}-{case['id']}" for case in cases if case["split"] == "holdout"]
    focused = [{"focus": item["focus"], "focus_reason": item["focus_reason"]} for item in _reflections_of(out) if "focus" in item]
    assert held and focused and all(item == {"focus": "references/alpha.md", "focus_reason": "under-tested"} for item in focused)
    text = json.dumps(focused) + "\n".join(_head(prompt) for prompt in state.prompts)
    assert not any(token in text for token in held)
    # A second run differs only in the holdout requests and the holdout scores. The focus choice must not move.
    other = tmp_path / "other"
    other.mkdir()
    target2, out2, state2, case_hash2, calls2 = _focus_start(other, make_target, write_draft, approvals, tag="other")
    state2.score_of = lambda text, split: 0.99 if split == "holdout" else _stalls(text, split)
    _ = run(target2, out2, MODEL, live=True, approve_cases=case_hash2, approve_budget=calls2, factory=state2.factory)
    shape = [(item.get("focus"), item.get("focus_reason")) for item in _reflections_of(out)]
    assert shape == [(item.get("focus"), item.get("focus_reason")) for item in _reflections_of(out2)]
    assert any(focus is not None for focus, _reason in shape)


@pytest.mark.ac("gh-244:AC-1")
def test_the_focus_counts_the_attempts_of_compatible_earlier_runs(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, first, earlier, case_hash, calls = _focus_start(tmp_path, make_target, write_draft, approvals)
    earlier.only = "references/alpha.md"
    _ = run(target, first, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=earlier.factory)
    assert any(item["changed"] == ["references/alpha.md"] for item in _reflections_of(first))
    second, state, case_hash, calls = _again(tmp_path, target, first, approvals)
    state.only, state.score_of = "SKILL.md", _stalls
    result = run(target, second, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    assert _history(result)["runs"] == [read(first / "run.json")["run_id"]]
    focused = {item["focus"] for item in _reflections_of(second) if "focus" in item}
    assert focused == {"references/beta.md"}, "alpha has earlier-run attempts, so beta is the least tested"


class _Killed(BaseException):
    """A kill between the `searched` checkpoint and the store write."""


@pytest.mark.ac("gh-243:AC-5")
def test_a_resume_at_the_searched_phase_without_a_history_block_writes_the_store(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)

    @final
    class KilledStore(FileHistoryStore):
        @override
        def write(self, skill: str, target_root: str, record: Mapping[str, object], run_id: str,
                  attempts: Sequence[Mapping[str, object]]) -> str | None:
            raise _Killed

    with pytest.raises(_Killed):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
                ports=Ports(history=KilledStore()))
    assert read(out / "run.json")["phase"] == "searched" and "history" not in cast(dict[str, object], read(out / "run.json")["search"])
    assert not _store(out).exists()
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert _history(result)["enabled"] is True and _history(result)["warnings"] == []
    lines = _store(out).read_text(encoding="utf-8").splitlines()
    assert [cast(dict[str, object], json.loads(line))["run_id"] for line in lines] == [read(out / "run.json")["run_id"]]
