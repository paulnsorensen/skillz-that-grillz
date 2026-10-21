"""Export writes redacted criterion verdicts and the `criteria_flags` of noisy criteria into report.json."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._records import read, write
from skillz_experiments.run._export import criteria_flags, export
from test_search_faults import _launch  # pyright: ignore[reportPrivateUsage]

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


def _judged(arm: str, case: str, **levels: int) -> dict[str, object]:
    return {"arm": arm, "case_id": case, "criteria": {name: {"level": level, "reason": "ok"} for name, level in levels.items()}}


def _flags_of(rows: list[dict[str, object]]) -> list[tuple[str, str]]:
    return [(str(flag["criterion"]), str(flag["reason"])) for flag in criteria_flags(rows)]


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_a_level_spread_of_two_across_repeats_of_one_arm_and_case_is_flagged() -> None:
    rows = [_judged("search", "c1", a=1), _judged("search", "c1", a=3)]
    assert criteria_flags(rows) == [{"criterion": "a", "reason": "spread", "case_id": "c1", "arm": "search", "levels": [1, 3]}]


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_a_level_spread_of_one_is_not_flagged() -> None:
    assert criteria_flags([_judged("search", "c1", a=3), _judged("search", "c1", a=4)]) == []


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_a_spread_across_different_arms_or_cases_is_not_flagged() -> None:
    rows = [_judged("baseline", "c1", a=1), _judged("search", "c1", a=4), _judged("search", "c2", a=4)]
    assert criteria_flags(rows) == []


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_two_search_candidates_on_one_case_are_not_repeats() -> None:
    rows = [_judged("search", "c1", a=1) | {"search_candidate": "x"}, _judged("search", "c1", a=4) | {"search_candidate": "y"},
            _judged("search", "c1", a=4) | {"search_candidate": "y"}]
    assert criteria_flags(rows) == []
    rows.append(_judged("search", "c1", a=1) | {"search_candidate": "y"})
    assert _flags_of(rows) == [("a", "spread")]


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_level_two_in_exactly_half_of_the_outcomes_is_flagged() -> None:
    rows = [_judged("baseline", "c1", a=2), _judged("search", "c1", a=3)]
    assert criteria_flags(rows) == [{"criterion": "a", "reason": "partly-met", "case_id": "c1", "level_2": 1, "outcomes": 2}]


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_level_two_in_under_half_of_the_outcomes_is_not_flagged() -> None:
    rows = [_judged("search", "c1", a=2), _judged("search", "c1", a=3), _judged("search", "c1", a=3)]
    assert criteria_flags(rows) == []


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_the_same_criterion_id_in_two_cases_is_two_criteria() -> None:
    rows = [_judged("search", "c1", a=2), _judged("search", "c2", a=3), _judged("search", "c3", a=4)]
    assert criteria_flags(rows) == [{"criterion": "a", "reason": "partly-met", "case_id": "c1", "level_2": 1, "outcomes": 1}]


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_flags_sort_by_criterion_and_outcomes_without_criteria_add_nothing() -> None:
    rows: list[dict[str, object]] = [
        {"arm": "search", "case_id": "c1", "score": 1.0}, {"arm": "search", "case_id": "c1", "criteria": None},
        _judged("search", "c1", b=2, a=1), _judged("search", "c1", b=2, a=4)]
    assert _flags_of(rows) == [("a", "spread"), ("b", "partly-met")]


def _exported(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
              rows: list[dict[str, object]]) -> dict[str, object]:
    out, _, launch = _launch(tmp_path, make_target, write_draft, approvals)
    assert launch()["phase"] == "complete"
    record = read(out / "run.json")
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    for target, extra in zip((item for item in outcomes if item.get("split") != "reflection"), rows):
        target["criteria"] = extra["criteria"]
    write(out / "run.json", record)
    _ = export(out, tmp_path / "exported")
    return read(tmp_path / "exported" / "report.json")


@pytest.mark.ac("judge-criteria-verdicts:AC-4")
@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac4_ac11_report_json_carries_redacted_criteria_and_the_flags(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    secret = "password=hunter2hunter2"
    rows = [
        {"criteria": {"clear": {"level": 2, "reason": f"the agent printed {secret}"}}},
        {"criteria": {"other": {"level": 4, "reason": "fine"}}},
    ]
    report = _exported(tmp_path, make_target, write_draft, approvals, cast(list[dict[str, object]], rows))
    judged = [item for item in cast(list[dict[str, object]], report["outcomes"]) if "criteria" in item]
    assert len(judged) == 2
    assert cast(dict[str, dict[str, object]], judged[1]["criteria"])["other"] == {"level": 4, "reason": "fine"}
    first = cast(dict[str, dict[str, object]], judged[0]["criteria"])["clear"]
    assert first["level"] == 2 and "hunter2" not in str(first["reason"]) and "[redacted]" in str(first["reason"])
    assert "hunter2" not in (tmp_path / "exported" / "report.json").read_text(encoding="utf-8")
    assert "criteria" not in cast(dict[str, object], judged[0].get("scores", {}))
    assert report["criteria_flags"] == [
        {"criterion": "clear", "reason": "partly-met", "case_id": judged[0]["case_id"], "level_2": 1, "outcomes": 1}]


@pytest.mark.ac("judge-criteria-verdicts:AC-11")
def test_ac11_report_json_has_an_empty_criteria_flags_list_without_criteria(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    report = _exported(tmp_path, make_target, write_draft, approvals, [])
    assert report["criteria_flags"] == []
