"""The run estimate shows the prompt-scaled reflection time and warns when it can use up the deadline (gh-571)."""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._budget import APPROVED_SECONDS, JOURNEY_SECONDS, MAX_CONCURRENT_CALLS, SECONDS_PER_CALL, Estimate
from skillz_experiments._candidate import TEXT_FILE_LIMIT, Candidate
from skillz_experiments._records import read
from skillz_experiments.run import Stop, run
from skillz_experiments.run._edit_plan import ReflectionNotes, editable_chars, reflection_time
from test_default_edit import MODEL
from test_press_autoimprove import Spy, prepare, tamper

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
HUGE_CHARS = 600_000
"""Editable characters under `PACKAGE_LIMIT` that make three reflection calls outlast the approved seconds cap."""


def _huge_helpers(target: Path) -> Path:
    """Add helper scripts of `HUGE_CHARS` characters in all, each under the text limit, and name them as editable."""
    line = "# " + "x" * 98 + "\n"
    # Each file holds about half the text limit, so the file count is HUGE_CHARS over the file size, rounded up.
    per_file = TEXT_FILE_LIMIT // 2 // len(line)
    names = [f"scripts/huge_{index}.py" for index in range(HUGE_CHARS // (per_file * len(line)) + 1)]
    for name in names:
        _ = (target / name).write_text(line * per_file)
    contract_path = target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_path.read_text()))
    contract["editable"] = ["SKILL.md", "scripts/echo.py", *names]
    _ = contract_path.write_text(json.dumps(contract))
    return target


def _sized(target: Path, out: Path, write_draft: Drafter) -> tuple[Stop, str]:
    """Approve the cases and return the `budget-unapproved` stop of an explicit `prose+cli` run and the case hash."""
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True, edit="prose+cli")
    case_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, live=True, edit="prose+cli", approve_cases=case_hash)
    assert budget.value.code == "budget-unapproved"
    return budget.value, case_hash


def _approve(target: Path, out: Path, stop: Stop, case_hash: str) -> dict[str, object]:
    """Approve the budget of `stop`, stop at `live-required`, and return the prepared run record."""
    with pytest.raises(Stop) as ready:
        _ = run(target, out, MODEL, edit="prose+cli", approve_cases=case_hash, approve_budget=cast(int, _estimate(stop)["calls"]))
    assert ready.value.code == "live-required"
    return read(out / "run.json")


def _estimate(stop: Stop) -> dict[str, object]:
    return cast(dict[str, object], stop.data["estimate"])


@pytest.mark.ac("gh-571:AC-1")
def test_the_reflection_block_charges_each_reflection_call_by_the_editable_size() -> None:
    block, _ = reflection_time(4, 2001, Estimate(100, 1000))
    assert block == {"calls": 4, "editable_chars": 2001, "seconds": 4 * (60 + 11)}


@pytest.mark.ac("gh-571:AC-1")
def test_the_budget_question_and_the_run_record_hold_the_reflection_block(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    target, out = _huge_helpers(make_target(tmp_path, helper=True)), tmp_path / "run"
    stop, case_hash = _sized(target, out, write_draft)
    record = _approve(target, out, stop, case_hash)
    estimate = cast(dict[str, object], record["estimate"])
    chars = editable_chars(cast(dict[str, str], record["seed"]), cast(list[str], record["editable"]))
    calls = cast(int, estimate["reflection_calls"])
    assert chars > HUGE_CHARS
    assert estimate["reflection"] == {"calls": calls, "editable_chars": chars,
                                      "seconds": calls * (60 + math.ceil(chars / 200))}
    assert _estimate(stop)["reflection"] == estimate["reflection"]


@pytest.mark.ac("gh-571:AC-2")
@pytest.mark.ac("gh-571:AC-3")
def test_the_warning_starts_when_the_projected_seconds_pass_the_cap() -> None:
    # 4 calls of 70 seconds replace a flat share of ceil(4 * 60 / 2) = 120 seconds: the projection adds 160 seconds.
    assert reflection_time(4, 2000, Estimate(100, APPROVED_SECONDS - 160))[1] is None
    warning = cast(str, reflection_time(4, 2000, Estimate(100, APPROVED_SECONDS - 159), advice="do this")[1])
    assert f"about {4 * 70} seconds" in warning and f"about {APPROVED_SECONDS + 1} of its {APPROVED_SECONDS}" in warning
    assert warning.endswith("before it spends its calls; do this")
    assert cast(str, reflection_time(4, 2000, Estimate(100, APPROVED_SECONDS))[1]).endswith("before it spends its calls")
    assert reflection_time(4, 2000, Estimate(100, APPROVED_SECONDS - 159, journey=True))[1] is None
    assert reflection_time(4, 2000, Estimate(100, JOURNEY_SECONDS - 159, journey=True))[1] is not None


@pytest.mark.ac("gh-571:AC-2")
def test_a_huge_editable_set_warns_in_the_budget_question_and_on_an_approved_run(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = _huge_helpers(make_target(tmp_path, helper=True)), tmp_path / "run"
    stop, case_hash = _sized(target, out, write_draft)
    warning = cast(str, stop.data["reflection_time_warning"])
    assert "the search can stop at the deadline" in warning and str(APPROVED_SECONDS) in warning
    assert "warning: " + warning in capsys.readouterr().err
    _ = _approve(target, out, stop, case_hash)
    assert "warning: " + warning in capsys.readouterr().err


@pytest.mark.ac("gh-571:AC-3")
def test_a_small_editable_set_has_no_reflection_time_warning(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    stop, _ = _sized(make_target(tmp_path, helper=True), tmp_path / "run", write_draft)
    assert "reflection_time_warning" not in stop.data
    assert "reflection" in _estimate(stop)


@pytest.mark.ac("gh-571:AC-4")
def test_the_editable_size_changes_no_call_count_second_count_or_budget(
        tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    small_target = make_target(tmp_path / "small", helper=True)
    small, small_hash = _sized(small_target, tmp_path / "small-run", write_draft)
    huge_target = _huge_helpers(make_target(tmp_path / "huge", helper=True))
    huge, huge_hash = _sized(huge_target, tmp_path / "huge-run", write_draft)
    assert "reflection_time_warning" in huge.data
    totals = [{key: value for key, value in _estimate(stop).items() if key != "reflection"} for stop in (small, huge)]
    assert totals[0] == totals[1]
    # The seconds stay the flat estimate: every call at SECONDS_PER_CALL over the concurrency, plus the sandbox time.
    flat = math.ceil(cast(int, totals[1]["calls"]) * SECONDS_PER_CALL / MAX_CONCURRENT_CALLS)
    assert totals[1]["seconds"] == flat + cast(int, totals[1].get("sandbox_seconds", 0))
    budgets = [_approve(root, out, stop, hashed)["budget"] for root, out, stop, hashed in (
        (small_target, tmp_path / "small-run", small, small_hash),
        (huge_target, tmp_path / "huge-run", huge, huge_hash))]
    assert budgets[0] == budgets[1]


@pytest.mark.ac("gh-571:AC-5")
def test_a_run_recorded_without_the_reflection_block_still_resumes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)

    def drop_block(record: dict[str, object]) -> None:
        del cast(dict[str, object], record["estimate"])["reflection"]

    tamper(prep.out, drop_block)
    result = run(prep.target, prep.out, MODEL, live=True, factory=Spy().factory)
    assert result["phase"] == "complete"


@pytest.mark.ac("gh-571:AC-1")
@pytest.mark.parametrize(("chars", "per_call"), [(0, 60), (1, 61), (199, 61), (200, 61), (201, 62), (400, 62), (401, 63)])
def test_the_reflection_seconds_round_each_call_up_to_a_whole_second_per_200_characters(chars: int, per_call: int) -> None:
    block, _ = reflection_time(3, chars, Estimate(100, 1000))
    assert block["seconds"] == 3 * per_call


@pytest.mark.ac("gh-571:AC-3")
def test_no_reflection_calls_add_no_time_and_no_warning() -> None:
    block, warning = reflection_time(0, 10**9, Estimate(100, APPROVED_SECONDS))
    assert block == {"calls": 0, "editable_chars": 10**9, "seconds": 0}
    assert warning is None


@pytest.mark.ac("gh-571:AC-2")
@pytest.mark.parametrize(("offer_prose", "names_prose"), [(True, True), (False, False)])
def test_a_time_warning_without_the_size_warning_gives_the_advice(offer_prose: bool, names_prose: bool) -> None:
    # 150000 characters stay under the size limit, but 10 calls of 810 seconds pass the cap.
    notes = ReflectionNotes.size(Candidate({"SKILL.md": "x" * 150_000}, ("SKILL.md",)), "prose+cli", offer_prose=offer_prose)
    _ = notes.time(10, Estimate(100, APPROVED_SECONDS))
    assert set(notes.warnings) == {"reflection_time_warning"}
    warning = notes.warnings["reflection_time_warning"]
    assert "name fewer files" in warning and ("--edit prose" in warning) is names_prose


@pytest.mark.ac("gh-571:AC-2")
def test_reflection_notes_return_and_print_both_warnings_for_a_very_large_seed(
        capsys: pytest.CaptureFixture[str]) -> None:
    seed = Candidate({"SKILL.md": "x" * 250_000}, ("SKILL.md",))
    notes = ReflectionNotes.size(seed, "prose+cli", offer_prose=True)
    assert set(notes.warnings) == {"reflection_warning"}
    block = notes.time(3, Estimate(100, APPROVED_SECONDS))
    assert block == {"calls": 3, "editable_chars": 250_000, "seconds": 3 * (60 + 1250)}
    assert set(notes.warnings) == {"reflection_warning", "reflection_time_warning"}
    err = capsys.readouterr().err
    assert all(f"warning: {text}" in err for text in notes.warnings.values())
    assert err.count("warning: ") == 2


@pytest.mark.ac("gh-571:AC-2")
def test_the_time_warning_leaves_the_advice_to_the_size_warning_when_both_fire() -> None:
    seed = Candidate({"SKILL.md": "x" * 250_000}, ("SKILL.md",))
    notes = ReflectionNotes.size(seed, "prose+cli", offer_prose=True)
    _ = notes.time(3, Estimate(100, APPROVED_SECONDS))
    assert "name fewer files" in notes.warnings["reflection_warning"]
    slow = notes.warnings["reflection_time_warning"]
    assert "name fewer files" not in slow and "--edit prose" not in slow
    assert slow.endswith("before it spends its calls")


@pytest.mark.ac("gh-571:AC-3")
def test_reflection_notes_for_a_small_seed_print_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    seed = Candidate({"SKILL.md": "x" * 100}, ("SKILL.md",))
    notes = ReflectionNotes.size(seed, "prose", offer_prose=False)
    block = notes.time(3, Estimate(100, 1000))
    assert block["editable_chars"] == 100 and notes.warnings == {}
    assert capsys.readouterr().err == ""


@pytest.mark.ac("gh-563:AC-4")
def test_a_sizing_stop_still_follows_the_size_warning_on_stderr(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = _huge_helpers(make_target(tmp_path, helper=True)), tmp_path / "run"
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True, edit="prose+cli", repeats=50)
    _ = capsys.readouterr()
    with pytest.raises(Stop) as sized:
        _ = run(target, out, MODEL, live=True, edit="prose+cli", repeats=50,
                approve_cases=cast(str, cases.value.data["case_hash"]))
    assert sized.value.code == "budget-unapproved" and "lower --repeats" in str(sized.value)
    assert "estimate" not in sized.value.data
    assert "more than the reflection limit" in capsys.readouterr().err


@pytest.mark.ac("gh-571:AC-5")
def test_a_resumed_run_with_an_altered_reflection_block_still_resumes(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    prep = prepare(tmp_path, make_target, write_draft, approvals)

    def alter(record: dict[str, object]) -> None:
        cast(dict[str, dict[str, int]], record["estimate"])["reflection"]["seconds"] += 99999

    tamper(prep.out, alter)
    assert run(prep.target, prep.out, MODEL, live=True, factory=Spy().factory)["phase"] == "complete"
