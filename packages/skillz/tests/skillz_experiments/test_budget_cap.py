"""`--approve-budget` raises a task run's call cap, the stop shows a repeats table, and a run records its unspent reserve (gh-588)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._budget import APPROVED_CALLS, JOURNEY_CALLS, JOURNEY_SECONDS, ceilings
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._records import read
from skillz_experiments.run import Stop, run, summary
from skillz_experiments.run._sizing import raised_cap
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
    assert type(unspent["calls"]) is int and 0 <= unspent["calls"] <= reserve
    assert isinstance(unspent["seconds"], (int, float)) and unspent["seconds"] >= 0
    assert summary(record)["budget"] == record["budget"]
