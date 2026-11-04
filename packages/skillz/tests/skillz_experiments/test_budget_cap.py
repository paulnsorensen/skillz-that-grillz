"""`--approve-budget` raises a task run's call cap, the stop shows a repeats table, and a run records its unspent reserve (gh-588)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._budget import (
    APPROVED_CALLS,
    APPROVED_SECONDS,
    JOURNEY_CALLS,
    JOURNEY_SECONDS,
    Budget,
    Estimate,
    ceilings,
)
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._records import read
from skillz_experiments.run import Stop, export, run, summary
from skillz_experiments.run._edit_plan import reflection_time
from skillz_experiments.run._open import _cap as recorded_cap  # pyright: ignore[reportPrivateUsage]
from skillz_experiments.run._sizing import raised_cap
from skillz_experiments.run._unspent import search_end, with_unspent
from test_run_search import MODEL, Search

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
BIG = 28


def _start(tmp_path: Path, make_target: Maker, write_draft: Drafter, families: int) -> tuple[Path, Path, str]:
    """Write a draft with `families` families and return the target, the run directory, and the case hash."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=families)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True)
    assert cases.value.code == "cases-unapproved"
    return target, out, cast(str, cases.value.data["case_hash"])


def _refused(target: Path, out: Path, case_hash: str, repeats: int | None = None, approve_budget: int | None = None) -> Stop:
    with pytest.raises(Stop) as refused:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, repeats=repeats, approve_budget=approve_budget)
    assert refused.value.code == "budget-unapproved"
    return refused.value


@pytest.mark.ac("gh-588:AC-4")
def test_a_plan_over_the_standard_cap_names_the_smallest_approval_that_fits(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, case_hash = _start(tmp_path, make_target, write_draft, BIG)
    stop = _refused(target, out, case_hash, repeats=3)
    smallest = cast(int, stop.data["smallest_approval"])
    assert APPROVED_CALLS < smallest <= JOURNEY_CALLS
    assert f"--approve-budget {smallest} is the smallest approved number that fits" in str(stop)
    estimate = cast(dict[str, object], stop.data["estimate"])
    assert APPROVED_CALLS < cast(int, estimate["calls"]) <= smallest and estimate["repeats"] == 3
    assert set(estimate) >= {"seconds", "search_calls", "holdout_cases", "reflection"}
    below = _refused(target, out, case_hash, repeats=3, approve_budget=smallest - 1)
    assert below.data["smallest_approval"] == smallest


@pytest.mark.ac("gh-588:AC-1")
def test_approve_budget_above_the_standard_cap_sizes_the_run_against_it(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, headroom_off: None) -> None:
    del headroom_off
    target, out, case_hash = _start(tmp_path, make_target, write_draft, BIG)
    smallest = cast(int, _refused(target, out, case_hash, repeats=3).data["smallest_approval"])
    state = Search(target)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, repeats=3, approve_budget=smallest,
                 factory=state.factory)
    assert result["phase"] == "complete"
    budget = cast(dict[str, object], read(out / "run.json")["budget"])
    assert budget["maximum"] == smallest > APPROVED_CALLS and budget["cap"] == smallest
    assert cast(int, budget["seconds"]) <= JOURNEY_SECONDS


@pytest.mark.ac("gh-588:AC-1")
def test_approve_budget_above_the_ceiling_on_a_task_run_is_refused(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, case_hash = _start(tmp_path, make_target, write_draft, 6)
    with pytest.raises(CodedError) as refused:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=JOURNEY_CALLS + 1)
    assert refused.value.code == "budget-cap-exceeded"


@pytest.mark.ac("gh-588:AC-1")
def test_the_raised_cap_scales_the_seconds_and_a_journey_run_keeps_its_ceiling() -> None:
    assert ceilings(False) == (APPROVED_CALLS, 7200)
    assert ceilings(False, 300) == (300, 10800)
    assert ceilings(False, JOURNEY_CALLS) == (JOURNEY_CALLS, JOURNEY_SECONDS)
    assert ceilings(True, 300) == (JOURNEY_CALLS, JOURNEY_SECONDS)
    with pytest.raises(ValueError, match="raised call cap"):
        _ = ceilings(False, JOURNEY_CALLS + 1)
    task, journey = (cast(Case, cast(object, SimpleNamespace(journey=value))) for value in (None, object()))
    assert raised_cap(300, [task]) == 300 and raised_cap(APPROVED_CALLS, [task]) == 0 and raised_cap(None, [task]) == 0
    assert raised_cap(JOURNEY_CALLS + 1, [journey]) == 0 and raised_cap(300, [journey]) == 0


@pytest.mark.ac("gh-588:AC-2")
def test_the_unapproved_stop_shows_the_repeats_table_unless_repeats_is_given(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, case_hash = _start(tmp_path, make_target, write_draft, BIG)
    stop = _refused(target, out, case_hash)
    table = cast(list[dict[str, object]], stop.data["repeats_table"])
    assert [row["repeats"] for row in table] == [1, 2, 3]
    assert [row["fits"] for row in table] == [True, True, False]
    first, second, third = table
    assert cast(int, first["calls"]) <= APPROVED_CALLS and cast(int, second["calls"]) <= APPROVED_CALLS
    assert set(first) >= {"holdout_calls", "search_calls", "calls", "seconds"}
    assert "does not fit" in str(stop) and cast(str, third["stop"]) in str(stop)
    assert "repeats table: repeats 1:" in str(stop)
    assert "repeats_table" not in _refused(target, out, case_hash, repeats=3).data


@pytest.mark.ac("gh-588:AC-2")
def test_the_approval_stop_of_a_plan_that_fits_shows_the_table_with_the_real_plans(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, case_hash = _start(tmp_path, make_target, write_draft, 6)
    stop = _refused(target, out, case_hash)
    table = cast(list[dict[str, object]], stop.data["repeats_table"])
    assert [row["fits"] for row in table] == [True, True, True]
    assert table[-1]["calls"] == cast(dict[str, object], stop.data["estimate"])["calls"]


@pytest.mark.ac("gh-588:AC-3")
def test_a_finished_run_records_its_unspent_reserve_and_the_summary_shows_it(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Callable[..., tuple[str, int]],
        headroom_off: None) -> None:
    del headroom_off
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=Search(target).factory)
    record = read(out / "run.json")
    unspent = cast(dict[str, object], cast(dict[str, object], record["budget"])["unspent"])
    reserve = cast(int, cast(dict[str, object], record["budget"])["reserve"])
    ended = cast(dict[str, int], record["search_end"])
    spent = cast(int, record["calls"]) - ended["calls"] - (cast(int, record["preflight_calls"]) - ended["preflight_calls"])
    assert type(unspent["calls"]) is int and unspent["calls"] == reserve - spent
    assert "search_end" not in summary(record)
    assert isinstance(unspent["seconds"], (int, float)) and unspent["seconds"] >= 0
    assert summary(record)["budget"] == record["budget"]
    _ = export(out, tmp_path / "exported")
    exported = cast(dict[str, object], read(tmp_path / "exported" / "report.json")["budget"])
    assert exported["unspent"] == unspent
    assert "search_end" not in read(tmp_path / "exported" / "report.json")


def _finished(ended: dict[str, float | int], **extra: object) -> dict[str, object]:
    return {"budget": {"reserve_seconds": 600}, "search_end": ended, **extra}


def _unspent(record: dict[str, object], budget: Budget) -> dict[str, object]:
    return cast(dict[str, object], with_unspent(record, budget)["unspent"])


@pytest.mark.ac("gh-588:AC-3")
def test_a_search_that_under_spends_does_not_count_as_unspent_reserve() -> None:
    budget = Budget(100, 1000.0, 20, 10, approved=True)
    ended = search_end(budget, {})
    assert ended["calls"] == 10
    for _ in range(20):
        budget.claim(holdout=True)
    budget.started -= 700
    assert _unspent(_finished(ended), budget) == {"calls": 0, "seconds": 0.0}


@pytest.mark.ac("gh-588:AC-3")
def test_a_partly_used_reserve_reports_what_the_gate_and_replay_left() -> None:
    budget = Budget(100, 1000.0, 20, 10, approved=True)
    ended = search_end(budget, {})
    for _ in range(5):
        budget.claim(holdout=True)
    budget.started -= 200
    unspent = _unspent(_finished(ended), budget)
    assert unspent["calls"] == 15
    assert unspent["seconds"] == pytest.approx(400.0, abs=2.0)


@pytest.mark.ac("gh-588:AC-3")
def test_the_preflight_that_a_resume_re_runs_does_not_spend_the_reserve() -> None:
    budget = Budget(100, 1000.0, 20, 10, approved=True)
    ended = search_end(budget, {"preflight_calls": 2})
    for _ in range(5):
        budget.claim(holdout=True)
    for _ in range(2):
        budget.claim()
    assert _unspent(_finished(ended, preflight_calls=4), budget)["calls"] == 15


@pytest.mark.ac("gh-588:AC-3")
def test_the_unspent_calls_stay_within_the_calls_the_run_may_still_make() -> None:
    budget = Budget(25, 1000.0, 20, 10, approved=True)
    ended = search_end(budget, {})
    budget.calls = 20
    assert _unspent(_finished(ended), budget)["calls"] == 5


@pytest.mark.ac("gh-588:AC-3")
@pytest.mark.parametrize("ended", [{"calls": 11, "seconds_left": 5.0}, {"calls": -1, "seconds_left": 5.0},
                                   {"calls": 10, "seconds_left": 7201.0}, {"calls": 10, "seconds_left": -1.0},
                                   {"calls": 10, "seconds_left": float("inf")}, {"calls": 10, "seconds_left": float("nan")},
                                   {"calls": 10.0, "seconds_left": 5.0}, {"calls": True, "seconds_left": 5.0}, "x"])
def test_an_out_of_range_search_end_is_a_tampered_record(ended: object) -> None:
    budget = Budget(100, 1000.0, 20, 10, approved=True)
    record: dict[str, object] = {"budget": {"reserve_seconds": 600}, "search_end": ended}
    with pytest.raises(CodedError) as stopped:
        _ = with_unspent(record, budget)
    assert stopped.value.code == "run-record-tampered"


@pytest.mark.ac("gh-588:AC-3")
def test_a_record_without_a_search_end_is_an_old_run_with_no_unspent() -> None:
    budget = Budget(100, 1000.0, 20, 10, approved=True)
    assert with_unspent({"budget": {"reserve_seconds": 600}}, budget) == {"reserve_seconds": 600}


@pytest.mark.ac("gh-588:AC-3")
def test_a_resume_with_less_time_left_than_the_snapshot_clamps_the_unspent_seconds() -> None:
    budget = Budget(100, 1000.0, 20, 10, approved=True)
    record: dict[str, object] = {"budget": {"reserve_seconds": 600}, "search_end": {"calls": 10, "seconds_left": 500.0}}
    budget.started -= 900
    unspent = _unspent(record, budget)
    assert 0.0 <= cast(float, unspent["seconds"]) <= 100.0
    budget.started -= 500
    assert _unspent(record, budget)["seconds"] == 0.0


@pytest.mark.ac("gh-588:AC-1")
def test_the_reflection_time_warning_uses_the_raised_cap() -> None:
    raised = 500
    seconds = ceilings(False, raised)[1]
    assert seconds > APPROVED_SECONDS
    over_standard = Estimate(100, APPROVED_SECONDS + 100 - 60, cap=raised)
    assert reflection_time(4, 2000, over_standard)[1] is None
    assert reflection_time(4, 2000, Estimate(100, seconds, cap=raised))[1] is not None


@pytest.mark.ac("gh-588:AC-1")
@pytest.mark.parametrize("cap", [1, APPROVED_CALLS, JOURNEY_CALLS + 1])
def test_a_recorded_cap_outside_the_raised_range_is_a_tampered_record(cap: int) -> None:
    with pytest.raises(Stop) as stopped:
        _ = recorded_cap({"cap": cap})
    assert stopped.value.code == "run-record-tampered"
    assert recorded_cap({}) == 0 and recorded_cap({"cap": 0}) == 0 and recorded_cap({"cap": JOURNEY_CALLS}) == JOURNEY_CALLS
