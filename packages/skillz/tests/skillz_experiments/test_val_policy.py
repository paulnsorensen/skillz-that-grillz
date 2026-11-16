"""The selectable validation policy through a real run: selection, resume, and the CLI.

The finalist tests are in `test_val_policy_finalists.py` and `test_val_policy_resume.py`. Each seed of the
five-seed study is in its own `test_val_policy_seed_<n>.py`, so each file stays below the test-time ceiling.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cases import CodedError
from skillz_experiments._cli import main
from skillz_experiments._records import read
from skillz_experiments.run import Stop, run
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.search._policy import FixedSubset
from test_run_search import MODEL, _allowance, _start, _validation  # pyright: ignore[reportPrivateUsage]

# The seed skill identity. In the controlled study, both policies keep the seed for every seed.
STUDY_WINNER = "9b157e5f3ad5c5d75f733b73999e488fe9e3844940659e4e41d0d73d606422e9"


def measure_seed(seed: int, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
                 approvals: Callable[..., tuple[str, int]]) -> tuple[int, int]:
    """Run one study seed under both policies. Return the full and subset metric calls.

    Each policy must keep the study winner and stay in its call allowance.
    """
    calls: dict[str, int] = {}
    for policy in ("full", "subset"):
        target, out, state, case_hash, approved = _start(tmp_path / policy, make_target, write_draft, approvals,
                                                         noise=seed, families=12)
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=approved,
                factory=state.factory, val_policy=policy)
        record = read(out / "run.json")
        kept = record.get("best_validated")
        winner = cast(str, kept["identity"]) if isinstance(kept, dict) else cast(str, record["seed_hash"])
        assert winner == STUDY_WINNER
        assert state.search_calls <= _allowance(out) + (0 if policy == "subset" else 2 * len(_validation(out)))
        calls[policy] = state.search_calls
    return calls["full"], calls["subset"]


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
def test_finalists_screen_in_proposal_order_against_new_incumbent(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

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
    assert first == validation and len(second) == (len(validation) + 1) // 2 < len(validation)
    assert cast(dict[str, object], record["best_validated"])["identity"] == proposals[0]


@pytest.mark.ac("gh-245:AC-2")
def test_equal_subset_mean_still_completes_finalist(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
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
