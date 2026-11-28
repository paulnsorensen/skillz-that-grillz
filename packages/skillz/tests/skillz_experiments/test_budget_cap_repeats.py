"""The search budget stop shows a repeats table, and a search approval can take one of its rows (gh-588)."""
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from _budget_cap_runs import Drafter, Maker, refused, start
from skillz_experiments._budget import APPROVED_CALLS
from skillz_experiments._cases import CodedError
from skillz_experiments._records import read
from skillz_experiments.run import Stop, run
from test_run_search import MODEL

pytestmark = pytest.mark.usefixtures("host_login", "cheap_record_writes")


@pytest.mark.ac("gh-588:AC-2")
def test_the_unapproved_stop_shows_the_repeats_table_unless_repeats_is_given(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, case_hash = start(tmp_path, make_target, write_draft, large=True)
    stop = refused(target, out, case_hash)
    table = cast(list[dict[str, object]], stop.data["repeats_table"])
    assert [row["repeats"] for row in table] == [1, 2, 3]
    assert [row["fits"] for row in table] == [True, True, False]
    first, second, third = table
    assert cast(int, first["calls"]) <= APPROVED_CALLS and cast(int, second["calls"]) <= APPROVED_CALLS
    assert set(first) >= {"holdout_calls", "search_calls", "calls", "seconds"}
    assert "does not fit" in str(stop) and cast(str, third["stop"]) in str(stop)
    assert "repeats table: repeats 1:" in str(stop)
    explicit = start(tmp_path / "explicit", make_target, write_draft, large=True)
    assert "repeats_table" not in refused(*explicit, repeats=3).data


@pytest.mark.ac("gh-588:AC-2")
def test_the_approval_stop_of_a_plan_that_fits_shows_the_table_with_the_real_plans(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, case_hash = start(tmp_path, make_target, write_draft)
    stop = refused(target, out, case_hash)
    table = cast(list[dict[str, object]], stop.data["repeats_table"])
    assert [row["fits"] for row in table] == [True, True, True]
    assert table[-1]["calls"] == cast(dict[str, object], stop.data["estimate"])["calls"]


@pytest.mark.ac("gh-588:AC-2")
def test_a_search_approval_takes_the_repeats_that_the_table_offers_and_freezes_them(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out, case_hash = start(tmp_path, make_target, write_draft, large=True)
    table = cast(list[dict[str, object]], refused(target, out, case_hash).data["repeats_table"])
    assert read(out / "run.json")["repeats_given"] is False and table[0]["repeats"] == 1
    with pytest.raises(Stop) as approved:
        _ = run(target, out, MODEL, search=True, repeats=1, approve_budget=cast(int, table[0]["calls"]))
    assert approved.value.code == "live-required"
    record = read(out / "run.json")
    assert record["plan_mode"] == "search" and record["repeats"] == 1 and record["repeats_given"] is True
    assert cast(dict[str, object], record["estimate"])["repeats"] == 1
    assert cast(dict[str, object], cast(dict[str, object], record["compatibility"])["fields"])["repeats"] == 1
    with pytest.raises(CodedError) as changed:
        _ = run(target, out, MODEL, live=True, search=True, repeats=2)
    assert changed.value.code == "run-config-differs"
