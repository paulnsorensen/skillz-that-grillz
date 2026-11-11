"""The selectable validation policy through a real run."""
from __future__ import annotations

import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import BudgetExhausted
from skillz_experiments._cases import CodedError
from skillz_experiments._cli import main
from skillz_experiments._intake import DRAFT_NAME
from skillz_experiments._records import read
from skillz_experiments.run import Stop, run
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.search import scored_runs, validation_complete
from skillz_experiments.search._policy import FixedSubset
from test_run_search import MODEL, Search, _allowance, _start, _validation  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("gh-245:AC-3")
def test_resume_rejects_changed_policy_and_keeps_omission(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    with pytest.raises(Stop, match="live model calls"):
        _ = run(target, out, MODEL, val_policy="subset", approve_cases=case_hash,
                approve_budget=calls, factory=state.factory)
    assert read(out / "run.json")["val_policy"] == "subset"
    with pytest.raises(CodedError) as changed:
        _ = run(target, out, MODEL, val_policy="full", factory=state.factory)
    assert changed.value.code == "run-config-differs"
    with pytest.raises(Stop, match="live model calls"):
        _ = run(target, out, MODEL, factory=state.factory)


@pytest.mark.ac("gh-245:AC-1")
@pytest.mark.ac("gh-245:AC-2")
@pytest.mark.ac("gh-245:AC-6")
def test_full_and_subset_use_full_planning_but_subset_scores_only_selected_cases(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    results: dict[str, dict[str, object]] = {}
    for policy in ("full", "subset"):
        target, out, state, case_hash, calls = _start(tmp_path / policy, make_target, write_draft, approvals,
                                                       families=12)
        state.score_of = lambda text, split: ((0.5 if split == "validation" else 0.2)
                                              if not text.startswith("improved") else
                                              (0.3 if split == "validation" else 0.4))
        if policy == "full":
            result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                         factory=state.factory)
        else:
            result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                         factory=state.factory, val_policy="subset")
        assert all(f"of {_allowance(out)} (" in prompt.split("\n", 1)[0] for prompt in state.prompts)
        results[policy] = read(out / "run.json")
        assert result["phase"] == "complete"
    full, subset = results["full"], results["subset"]
    assert full["estimate"] == subset["estimate"]
    outcomes = cast(list[dict[str, object]], subset["outcomes"])
    validation = sorted(_validation(tmp_path / "subset" / "run"))
    picked = random.Random(cast(int, subset["split_seed"])).sample(range(len(validation)), (len(validation) + 1) // 2)
    selected = {validation[index] for index in picked}
    losing = next(item["proposed"] for item in outcomes if item.get("split") == "reflection" and
                  isinstance(item.get("proposed"), str))
    scored = {item["case_id"] for item in outcomes if item.get("split") == "validation" and
              item.get("search_candidate") == losing}
    assert scored == selected
    full_rows = cast(list[dict[str, object]], full["outcomes"])
    full_scored = {item["case_id"] for item in full_rows if item.get("split") == "validation" and
                   item.get("search_candidate") == losing}
    assert full_scored == set(validation)


@pytest.mark.ac("gh-245:AC-3")
def test_cli_passes_policy_to_completed_run_resume(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], capsys: pytest.CaptureFixture[str]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
            factory=state.factory, val_policy="subset")
    code = main(["run", "--target", str(target), "--out", str(out), "--model", MODEL,
                 "--val-policy", "full"])
    assert code == 1
    assert '"code": "run-config-differs"' in capsys.readouterr().err


@pytest.mark.ac("gh-245:AC-4")
def test_five_seed_policy_measurement(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    rows: list[tuple[int, int, str, int, str]] = []
    for seed in range(5):
        measured: dict[str, tuple[int, str]] = {}
        for policy in ("full", "subset"):
            target, out, state, case_hash, calls = _start(tmp_path / f"{seed}-{policy}", make_target,
                                                           write_draft, approvals, noise=seed, families=12)
            _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                    factory=state.factory, val_policy=policy)
            record = read(out / "run.json")
            kept = record.get("best_validated")
            winner = cast(str, kept["identity"]) if isinstance(kept, dict) else cast(str, record["seed_hash"])
            assert state.search_calls <= _allowance(out) + (0 if policy == "subset" else 2 * len(_validation(out)))
            measured[policy] = state.search_calls, winner
        rows.append((seed, *measured["full"], *measured["subset"]))
    assert [(seed, full_calls, subset_calls) for seed, full_calls, _, subset_calls, _ in rows] == [
        (0, 86, 62), (1, 84, 62), (2, 108, 60), (3, 95, 68), (4, 68, 70)]
    expected_winner = "9b157e5f3ad5c5d75f733b73999e488fe9e3844940659e4e41d0d73d606422e9"
    assert all(full_winner == subset_winner == expected_winner for _, _, full_winner, _, subset_winner in rows)


@pytest.mark.ac("gh-245:AC-2")
def test_qualifying_finalist_completes_full_validation_before_promotion(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                 factory=state.factory, val_policy="subset")
    record = read(out / "run.json")
    kept = cast(dict[str, object], record["best_validated"])
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    validation = set(_validation(out))
    scored = {(item["case_id"], item["repeat"]) for item in outcomes
              if item.get("split") == "validation" and item.get("search_candidate") == kept["identity"]}
    assert scored == {(case, repeat) for case in validation for repeat in range(VALIDATION_REPEATS)}
    assert result["phase"] == "complete" and kept["identity"] != record["seed_hash"]


@pytest.mark.ac("gh-245:AC-2")
def test_gepa_policy_selects_only_fixed_positions_and_scores_them() -> None:
    class Loader:
        def all_ids(self) -> list[int]:
            return [10, 20, 30]

    class State:
        prog_candidate_val_subscores: list[dict[int, float]] = [{10: 0.2, 30: 0.4}, {10: 0.8, 30: 0.6}]

        def get_program_average_val_subset(self, program_idx: int) -> tuple[float, int]:
            return sum(self.prog_candidate_val_subscores[program_idx].values()) / 2, 2

    policy = FixedSubset((0, 2))
    state = State()
    assert policy.get_eval_batch(Loader(), state) == [10, 30]
    assert policy.get_best_program(state) == 1
    assert policy.get_valset_score(1, state) == 0.7


@pytest.mark.ac("gh-245:AC-2")
@pytest.mark.ac("gh-245:AC-3")
def test_interrupted_finalist_resumes_without_new_proposals(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    interrupted = False

    def fault(_number: int) -> Exception | None:
        nonlocal interrupted
        record = read(out / "run.json")
        search = record.get("search")
        if not interrupted and isinstance(search, dict) and cast(dict[str, object], search).get("subset_search_done") is True:
            interrupted = True
            return CodedError("other-code", "finalist interrupted")
        return None

    state.evaluate_fault = fault
    with pytest.raises(CodedError, match="finalist interrupted"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                factory=state.factory, val_policy="subset")
    assert interrupted
    charged = [item for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
               if item.get("arm") == "search" and item.get("split") in ("train", "validation")]
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
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    interrupted = False

    def fault(_number: int) -> Exception | None:
        nonlocal interrupted
        search = read(out / "run.json").get("search")
        done = isinstance(search, dict) and cast(dict[str, object], search).get("subset_search_done") is True
        if not interrupted and not done and state.proposals and state.search_log[-1][1] == "validation":
            interrupted = True
            return CodedError("other-code", "search interrupted")
        return None

    state.evaluate_fault = fault
    with pytest.raises(CodedError, match="search interrupted"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                factory=state.factory, val_policy="subset")
    assert interrupted
    record = read(out / "run.json")
    rows = cast(list[dict[str, object]], record["outcomes"])
    charged = [item for item in rows if item.get("arm") == "search" and
               item.get("split") in ("train", "validation")]
    assert len(charged) == state.search_calls
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    assert state.search_calls <= _allowance(out)


@pytest.mark.ac("gh-245:AC-2")
def test_finalist_provider_error_records_failed_attempt_and_completes_slots(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    faulted = False

    def fault(_number: int) -> Exception | None:
        nonlocal faulted
        search = read(out / "run.json").get("search")
        if not faulted and isinstance(search, dict) and cast(dict[str, object], search).get("subset_search_done") is True:
            faulted = True
            return OSError("finalist provider failed")
        return None

    state.evaluate_fault = fault
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                 factory=state.factory, val_policy="subset")
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
    charged = [item for item in rows if item.get("arm") == "search" and item.get("split") in ("train", "validation")]
    assert len(charged) == state.search_calls <= _allowance(out)


@pytest.mark.ac("gh-245:AC-2")
def test_finalist_budget_stop_keeps_only_fully_validated_winner(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2
    faulted = False

    def fault(_number: int) -> Exception | None:
        nonlocal faulted
        search = read(out / "run.json").get("search")
        if not faulted and isinstance(search, dict) and cast(dict[str, object], search).get("subset_search_done") is True:
            faulted = True
            return BudgetExhausted("finalist budget spent")
        return None

    state.evaluate_fault = fault
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                 factory=state.factory, val_policy="subset")
    assert faulted and result["phase"] == "complete"
    record = read(out / "run.json")
    assert cast(dict[str, object], record["search"])["reason"] == "budget-exhausted-seed-retained"
    assert record.get("best_validated") is None
    assert state.search_calls <= _allowance(out)


@pytest.mark.ac("gh-245:AC-2")
def test_repeated_charged_interruptions_stop_at_search_ceiling(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, _split: 0.8 if text.startswith("improved") else 0.2

    def fault(_number: int) -> Exception | None:
        search = read(out / "run.json").get("search")
        if isinstance(search, dict) and cast(dict[str, object], search).get("subset_search_done") is True:
            return CodedError("other-code", "charged interruption")
        return None

    state.evaluate_fault = fault
    with pytest.raises(CodedError, match="charged interruption"):
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
                factory=state.factory, val_policy="subset")
    ceiling = _allowance(out)
    assert state.search_calls < ceiling
    proposals = state.proposals
    while state.search_calls < ceiling:
        with pytest.raises(CodedError, match="charged interruption"):
            _ = run(target, out, MODEL, live=True, factory=state.factory)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete" and state.proposals == proposals
    assert state.search_calls == ceiling
    rows = cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
    assert sum(item.get("arm") == "search" and item.get("split") in ("train", "validation") for item in rows) == ceiling


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
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
            factory=state.factory, val_policy="subset")
    record = read(out / "run.json")
    validation = _validation(out)
    assert len(validation) % 2 == 1
    proposed = next(item["proposed"] for item in cast(list[dict[str, object]], record["outcomes"])
                    if item.get("split") == "reflection" and isinstance(item.get("proposed"), str))
    calls_for_candidate = [(item["case_id"], item["repeat"]) for item in cast(list[dict[str, object]], record["outcomes"])
                           if item.get("split") == "validation" and item.get("search_candidate") == proposed]
    assert len(calls_for_candidate) == 2 * ((len(validation) + 1) // 2)
    assert {repeat for _, repeat in calls_for_candidate} == {0, 1}


@pytest.mark.ac("gh-245:AC-2")
def test_finalists_screen_in_proposal_order_against_new_incumbent(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)

    def score(text: str, split: str) -> float:
        if text.startswith("improved 1"):
            return 0.7 if split == "train" else 0.8
        if text.startswith("improved 2"):
            return 0.9 if split == "train" else 0.5
        return 0.2

    state.score_of = score
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
            factory=state.factory, val_policy="subset")
    record = read(out / "run.json")
    proposals = [item["proposed"] for item in cast(list[dict[str, object]], record["outcomes"])
                 if item.get("split") == "reflection" and isinstance(item.get("proposed"), str)]
    assert len(proposals) >= 2
    validation = set(_validation(out))
    rows = cast(list[dict[str, object]], record["outcomes"])
    first = {item["case_id"] for item in rows if item.get("split") == "validation" and item.get("search_candidate") == proposals[0]}
    second = {item["case_id"] for item in rows if item.get("split") == "validation" and item.get("search_candidate") == proposals[1]}
    assert first == validation and len(second) == (len(validation) + 1) // 2
    assert cast(dict[str, object], record["best_validated"])["identity"] == proposals[0]


@pytest.mark.ac("gh-245:AC-2")
def test_equal_subset_mean_still_completes_finalist(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    state.score_of = lambda text, split: 0.8 if split == "train" and text.startswith("improved") else 0.5
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls,
            factory=state.factory, val_policy="subset")
    record = read(out / "run.json")
    proposed = next(item["proposed"] for item in cast(list[dict[str, object]], record["outcomes"])
                    if item.get("split") == "reflection" and isinstance(item.get("proposed"), str))
    validation = set(_validation(out))
    scored = {(item["case_id"], item["repeat"]) for item in cast(list[dict[str, object]], record["outcomes"])
              if item.get("split") == "validation" and item.get("search_candidate") == proposed}
    assert scored == {(case, repeat) for case in validation for repeat in range(VALIDATION_REPEATS)}
    assert record.get("best_validated") is None
