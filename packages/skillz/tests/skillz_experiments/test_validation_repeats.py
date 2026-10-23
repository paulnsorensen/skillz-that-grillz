"""Repeated validation runs: the validation policy, its budget accounting, and the search stop trigger (gh-500)."""
from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from skillz_experiments._budget import Budget
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._records import read, write
from skillz_experiments.gate import DEFAULT_STATISTICS
from skillz_experiments.run import Stop, _outcomes, _plan, _search_phase, _session, export, run
from skillz_experiments.run._export import criteria_flags
from skillz_experiments.run._leaderboard import Leaderboard
from skillz_experiments.run._open import validation_policy
from skillz_experiments.run._search_phase import SearchPhase
from skillz_experiments.run._state import RunState
from skillz_experiments.search import _history, add_validation_scores, attempts, complete_means, search, search_allowance
from skillz_experiments.search import validation_complete
from test_run_search import MODEL, Provider, _interrupt, _start  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


@pytest.mark.ac("gh-500:AC-5")
def test_the_plan_metric_and_its_admission_count_each_validation_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_plan, "VALIDATION_REPEATS", 2)
    seen: list[tuple[int, int]] = []

    def spy(metric: int, validation: int) -> int:
        seen.append((metric, validation))
        return search_allowance(metric, validation)

    monkeypatch.setattr(_plan, "search_allowance", spy)
    plan = _plan._plan(3, 2, 2, 1, 1)  # pyright: ignore[reportPrivateUsage]
    assert plan.metric_calls == _plan.SEARCH_CASE_FACTOR * (3 + 2 * 2) == 28
    # The admission check and the overshoot margin inside `search_allowance` see 2 runs for each of the 2 cases.
    assert seen == [(28, 4)]


@pytest.mark.ac("gh-500:AC-5")
def test_the_search_ceiling_adds_back_every_seed_validation_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_search_phase, "VALIDATION_REPEATS", 2)
    cases = [SimpleNamespace(identifier=f"v{index}") for index in range(3)]

    def cases_for(split: str) -> list[SimpleNamespace]:
        return cases if split == "validation" else []

    state = SimpleNamespace(record={"estimate": {"metric_calls": 40, "headroom": {"outcomes": 5}}}, cases_for=cases_for)
    ceiling = SearchPhase.search_ceiling(cast(SearchPhase, cast(object, SimpleNamespace(_state=state))))
    # The allowance and its overshoot count 6 validation runs; the seed term adds the 5 train and 6 validation outcomes.
    assert ceiling == search_allowance(40, 6) + 5 + 6 == 35


def _no_gain(proposals: list[str]) -> tuple[Callable[[dict[str, str], object], tuple[float, dict[str, object]]],
                                           Callable[[dict[str, str], Mapping[str, Sequence[Mapping[str, object]]], list[str]],
                                                    dict[str, str]]]:
    """Return a seed-only search: the seed scores 0.5 and every child scores 0, so no child is ever validated."""

    def evaluate(candidate: dict[str, str], _example: object) -> tuple[float, dict[str, object]]:
        return (0.5 if candidate["SKILL.md"] == "seed" else 0.0), {"task_correct": 0.0}

    def propose(_candidate: dict[str, str], _feedback: Mapping[str, Sequence[Mapping[str, object]]],
                components: list[str]) -> dict[str, str]:
        proposals.append("called")
        return {name: f"child {len(proposals)}" for name in components}

    return evaluate, propose


@pytest.mark.ac("gh-500:AC-5")
@pytest.mark.ac("gh-500:AC-7")
@pytest.mark.parametrize(("repeats", "expected"), [(1, 4), (2, 3)])
def test_the_engine_debits_each_validation_repeat_and_nothing_for_a_reused_pair(repeats: int, expected: int) -> None:
    """The seed validation pass debits `repeats` runs, each new train pair 1, and each parent repeat 0.

    With 2 repeats: 2 (seed on v1) + 2 (seed on t1, t2) + 2 per child reach 10 after 3 proposals. A debited parent
    repeat would stop after 2 proposals. One validation run per case reaches 10 only after 4 proposals.
    """
    proposals: list[str] = []
    evaluate, propose = _no_gain(proposals)
    assert search({"SKILL.md": "seed"}, "prose", ["t1", "t2"], ["v1"], evaluate, propose, metric_calls=10,
                  validation_repeats=repeats) == "metric-count"
    assert len(proposals) == expected


@pytest.mark.ac("gh-500:AC-7")
def test_the_engine_names_a_spent_call_share_as_the_stop() -> None:
    proposals: list[str] = []
    evaluate, propose = _no_gain(proposals)
    assert search({"SKILL.md": "seed"}, "prose", ["t1", "t2"], ["v1"], evaluate, propose, metric_calls=100,
                  spent=lambda: len(proposals) >= 2) == "call-share"
    assert len(proposals) == 2


@pytest.mark.ac("gh-500:AC-7")
def test_a_budget_stop_names_the_call_cap_the_search_deadline_or_the_global_deadline() -> None:
    live, expired = Budget(10, 60.0), Budget(10, 60.0, started=time.monotonic() - 120.0)
    assert _session._budget_trigger(live, False) == "call-cap"  # pyright: ignore[reportPrivateUsage]
    # The deadline passed while the gate time was reserved, and the global deadline is still ahead.
    assert _session._budget_trigger(live, True) == "search-deadline"  # pyright: ignore[reportPrivateUsage]
    assert _session._budget_trigger(expired, True) == "deadline"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.ac("gh-500:AC-7")
@pytest.mark.parametrize(("stopped", "trigger", "reason"), [("metric-count", "metric-count", "validation-mean"),
                                                          ("call-share", "call-share", "validation-mean"),
                                                          ("stall", "stall", "converged-stalled"),
                                                          ("idle", "stall", "validation-mean")])
def test_the_run_records_the_engine_stop_as_the_search_stop_trigger(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, monkeypatch: pytest.MonkeyPatch,
        stopped: str, trigger: str, reason: str) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)

    def engine(*_args: object, **_kwargs: object) -> str:
        return stopped

    monkeypatch.setattr(_session, "pareto_search", engine)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    recorded = cast(dict[str, object], read(out / "run.json")["search"])
    assert (recorded["stop_trigger"], recorded["reason"]) == (trigger, reason)
    assert result["phase"] == "complete"


def _prepared(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> tuple[Path, Path, object]:
    """Prepare a run without live calls. Return the target, the run directory, and the fake provider factory."""
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    with pytest.raises(Stop):
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls)
    return target, out, state.factory


@pytest.mark.ac("gh-500:AC-8")
@pytest.mark.parametrize("policy", [{"repeats": 7}, None])
def test_a_resume_with_another_or_no_validation_policy_stops_before_the_engine_hash_check(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        policy: dict[str, object] | None) -> None:
    target, out, factory = _prepared(tmp_path, make_target, write_draft, approvals)
    record = read(out / "run.json")
    assert record["validation_policy"] == validation_policy() == {"repeats": _plan.VALIDATION_REPEATS}
    if policy is None:
        del record["validation_policy"]
    else:
        record["validation_policy"] = policy
    # A changed engine hash would stop with the generic evaluator error; the policy stop comes first.
    record["engine_hash"] = "0" * 64
    write(out / "run.json", record)
    with pytest.raises(CodedError) as stopped:
        _ = run(target, out, MODEL, live=True, factory=cast(Callable[..., object], factory))  # pyright: ignore[reportArgumentType]
    assert stopped.value.code == "validation-policy-differs"
    assert ("no validation_policy" if policy is None else "{'repeats': 7}") in str(stopped.value)


@pytest.mark.ac("gh-500:AC-8")
def test_a_completed_run_without_a_validation_policy_still_summarises_and_exports(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    fields = cast(dict[str, dict[str, object]], record["compatibility"])["fields"]
    assert fields["validation_policy"] == validation_policy()
    del record["validation_policy"]
    del fields["validation_policy"]
    write(out / "run.json", record)
    assert run(target, out, MODEL, live=True, factory=state.factory)["phase"] == "complete"
    assert "export" in export(out, tmp_path / "export")


def _row(candidate: str, case: str, score: float, repeat: int | None, *, failed: bool = False) -> dict[str, object]:
    """Return one search validation outcome. A failed one has the placeholder score that no decision reads."""
    row: dict[str, object] = {"arm": "search", "split": "validation", "case_id": case, "search_candidate": candidate,
                              "score": 0.0 if failed else score}
    if repeat is not None:
        row["repeat"] = repeat
    return row | ({"status": "evaluation-failed", "failure": "provider fell over"} if failed else {})


def _reflection(proposed: str) -> dict[str, object]:
    return {"arm": "search", "split": "reflection", "proposed": proposed, "parent": "p", "components_digest": "d"}


@pytest.mark.ac("gh-500:AC-1")
@pytest.mark.ac("gh-500:AC-6")
def test_gepa_gets_the_mean_of_two_distinct_repeat_calls_for_every_candidate_and_validation_case(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Each validation run scores 1.0, then 0.0. A run served from the reuse cache would repeat the first score."""
    lock = threading.Lock()
    counts: Counter[tuple[str, str]] = Counter()
    real = Provider.evaluate

    def alternating(self: Provider, candidate: Candidate, case: Case, *, holdout: bool = False,
                    checks: object = None) -> dict[str, object]:
        result = real(self, candidate, case, holdout=holdout, checks=checks)
        if case.split == "validation" and not holdout:
            with lock:
                index = counts[(candidate.identity, case.identifier)]
                counts[(candidate.identity, case.identifier)] += 1
            result["score"] = 1.0 if index % 2 == 0 else 0.0
        return result

    monkeypatch.setattr(Provider, "evaluate", alternating)
    seen: list[tuple[str, float]] = []
    engine = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        evaluate = cast(Callable[[dict[str, str], object], tuple[float, dict[str, object]]], args[4])
        validation = set(cast(list[object], args[3]))

        def recorded(candidate: dict[str, str], example: object) -> tuple[float, dict[str, object]]:
            score, feedback = evaluate(candidate, example)
            if example in validation:
                seen.append((cast(str, example), score))
            return score, feedback

        forwarded: tuple[Any, ...] = (*args[:4], recorded, *args[5:])  # pyright: ignore[reportExplicitAny]
        return engine(*forwarded, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    rows = [item for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
            if item.get("arm") == "search" and item.get("split") == "validation"]
    by_pair: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for item in rows:
        by_pair.setdefault((cast(str, item["search_candidate"]), cast(str, item["case_id"])), []).append(
            (cast(int, item.get("repeat")), cast(float, item["score"])))
    # Two candidates at least: the seed and one validated proposal. Each pair has two provider calls and two outcomes.
    assert len({identity for identity, _ in by_pair}) >= 2
    # The headroom pass runs the seed repeats in parallel, so a repeat index does not fix which score it got.
    assert all(sorted(repeat for repeat, _ in found) == [0, 1] and sorted(score for _, score in found) == [0.0, 1.0]
               for found in by_pair.values())
    assert counts == Counter({pair: 2 for pair in by_pair})
    # GEPA receives the per-case mean of every validated candidate, and no candidate is held back from it.
    assert seen and all(score == 0.5 for _, score in seen)


@pytest.mark.ac("gh-500:AC-5")
def test_the_physical_search_runs_stay_within_the_planned_search_share(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals, families=12)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    estimate = cast(dict[str, Any], read(out / "run.json")["estimate"])  # pyright: ignore[reportExplicitAny]
    planned = cast(int, estimate["metric_calls"]) + cast(int, estimate["headroom"]["outcomes"])
    assert state.proposals >= 2
    assert 0 < len(state.search_log) <= planned


@pytest.mark.ac("gh-500:AC-9")
def test_two_repeats_give_their_mean_count_in_a_stall_window_only_when_both_ran_and_keep_their_level_spread() -> None:
    assert add_validation_scores({}, [_row("c", "v", 1.0, 0), _row("c", "v", 0.0, 1)]) == {"c": {"v": 0.5}}
    seed = [_row("seed", "v", 0.25, 0), _row("seed", "v", 0.25, 1)]
    half = [*seed, _reflection("c"), _row("c", "v", 1.0, 0)]
    assert _outcomes.stall_observations(half, {"v"}, 2) == [0.0]
    assert _outcomes.stall_observations([*half, _row("c", "v", 1.0, 1)], {"v"}, 2) == [0.75]
    judged = [{"arm": "search", "case_id": "v", "search_candidate": "c", "repeat": repeat,
               "criteria": {"answer": {"level": level, "reason": "ok"}}} for repeat, level in ((0, 1), (1, 3))]
    assert criteria_flags(cast(list[dict[str, object]], judged)) == [
        {"criterion": "answer", "reason": "spread", "case_id": "v", "arm": "search", "levels": [1, 3]}]


@pytest.mark.ac("gh-500:AC-11")
def test_a_failed_repeat_counts_as_attempted_and_a_case_scores_its_scored_repeats_only() -> None:
    one = [_row("c", "v1", 0.8, 0), _row("c", "v1", 0.0, 1, failed=True), _row("c", "v2", 0.4, 0), _row("c", "v2", 0.6, 1)]
    assert validation_complete(one, "c", {"v1", "v2"}, 2)
    assert add_validation_scores({}, one) == {"c": {"v1": 0.8, "v2": 0.5}}
    assert complete_means(one, {"v1", "v2"}, 2) == {"c": pytest.approx(0.65)}
    both = [_row("c", "v1", 0.0, 0, failed=True), _row("c", "v1", 0.0, 1, failed=True), *one[2:]]
    assert validation_complete(both, "c", {"v1", "v2"}, 2)
    assert add_validation_scores({}, both) == {"c": {"v2": 0.5}}
    assert complete_means(both, {"v1", "v2"}, 2) == {}
    assert not validation_complete(one[:3], "c", {"v1", "v2"}, 2)


def _leaderboard_state(outcomes: list[dict[str, object]]) -> RunState:
    seed = Candidate({"SKILL.md": "x"}, ("SKILL.md",))
    cases = [SimpleNamespace(identifier="v")]

    def cases_for(split: str) -> list[SimpleNamespace]:
        return cases if split == "validation" else []

    state = SimpleNamespace(lock=threading.RLock(), statistics=DEFAULT_STATISTICS, outcomes=outcomes, record={},
                            seed=seed, checkpoint=lambda: None, cases_for=cases_for)
    return cast(RunState, cast(object, state))


@pytest.mark.ac("gh-500:AC-11")
def test_the_leaderboard_history_and_stall_readers_take_completion_from_validation_complete(
        monkeypatch: pytest.MonkeyPatch) -> None:
    seed = [_row("seed", "v", 0.25, 0), _row("seed", "v", 0.25, 1)]
    outcomes = [*seed, _reflection("c"), _row("c", "v", 1.0, 0), _row("c", "v", 1.0, 1)]
    state = _leaderboard_state(outcomes)

    def readers() -> tuple[object, ...]:
        return (
            Leaderboard(state)._means(["v"]),  # pyright: ignore[reportPrivateUsage]
            attempts(outcomes, ("p", "seed"), ["v"], 2)[0]["validation_delta"],
            _outcomes.stall_observations(outcomes, {"v"}, 2))
    assert readers() == ({"seed": 0.25, "c": 1.0}, 0.75, [0.75])
    asked: list[str] = []

    def never(found: Sequence[Mapping[str, object]], candidate: str, cases: object, repeats: int) -> bool:
        del found, cases, repeats
        asked.append(candidate)
        return False

    monkeypatch.setattr(_history, "validation_complete", never)
    monkeypatch.setattr(_outcomes, "validation_complete", never)
    # With the one completion test saying no, no reader finds a complete candidate.
    assert readers() == ({}, None, [0.0])
    assert set(asked) == {"seed", "c"}


@pytest.mark.ac("gh-500:AC-10")
def test_a_resumed_headroom_pass_scores_only_the_missing_seed_repeat(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, _ = _interrupt(tmp_path, make_target, write_draft, approvals)
    record = read(out / "run.json")
    seed = cast(str, record["seed_hash"])
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    seeded = [item for item in outcomes if item.get("search_candidate") == seed and item.get("split") == "validation"]
    assert seeded and Counter(cast(int, item["repeat"]) for item in seeded) == {0: len(seeded) // 2, 1: len(seeded) // 2}
    dropped = next(item for item in seeded if item["repeat"] == 1)
    outcomes.remove(dropped)
    del record["headroom"]
    write(out / "run.json", record)
    state.search_log.clear()
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    # The case with one repeat missing counts as missing, and only that repeat runs again.
    assert [entry for entry in state.search_log if entry[0] == seed] == [(seed, "validation", dropped["case_id"])]
    again = [item for item in cast(list[dict[str, object]], read(out / "run.json")["outcomes"])
             if item.get("search_candidate") == seed and item.get("case_id") == dropped["case_id"]]
    assert sorted(cast(int, item["repeat"]) for item in again) == [0, 1]
