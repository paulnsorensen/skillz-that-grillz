"""The headroom pass records each seed case with its marks, stops on an unscorable case, and warns on the rest."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from skillz_experiments._cases import Case, CodedError
from skillz_experiments._records import read, write
from skillz_experiments.run._export import export
from skillz_experiments.run._signals import seed_signals

from test_headroom import _cases, _go, _grants, _headroom, _saturate_train  # pyright: ignore[reportPrivateUsage]
from test_run_search import Search, _start  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


def _zero_seed(state: Search) -> None:
    """Score the seed 0 on every case. Every improved text keeps the default score."""
    state.score_of = lambda text, _split: 0.0 if "improved" not in text else None


def _case(identifier: str, split: str = "train", family: str | None = None) -> Case:
    return cast(Case, cast(object, SimpleNamespace(identifier=identifier, split=split, family=family or identifier)))


def _item(score: float, *, fault: str | None = None, status: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"score": score}
    if fault is not None:
        item["gate_fault"] = fault
    if status is not None:
        item["status"] = status
    return item


def _found(case: Case, *items: dict[str, object]) -> dict[tuple[object, object, object], Mapping[str, object]]:
    repeats = [None] if case.split == "train" else [0, 1]
    return {(case.split, case.identifier, repeat): item for repeat, item in zip(repeats, items, strict=True)}


@pytest.mark.ac("gh-656:AC-1")
def test_the_headroom_block_records_each_case_with_its_repeat_scores_status_and_mark(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    state.extra = {"status": "gate-failed"}
    state.case_score_of = lambda _text, split, _case: 1.0 if split == "train" else None
    with pytest.raises(CodedError):
        _ = _go(state, target, out, case_hash, calls)
    recorded = cast(dict[str, dict[str, Any]], _headroom(out)["cases"])  # pyright: ignore[reportExplicitAny]
    frozen = {str(case["id"]): case for case in _cases(out, "train", "validation")}
    assert set(recorded) == set(frozen)
    for name, entry in recorded.items():
        repeats = 1 if frozen[name]["split"] == "train" else 2
        assert entry["split"] == frozen[name]["split"] and entry["family"] == frozen[name]["family"]
        assert len(entry["scores"]) == len(entry["status"]) == len(entry["gate_fault"]) == repeats  # pyright: ignore[reportAny]
        assert entry["status"] == ["gate-failed"] * repeats and entry["gate_fault"] == [None] * repeats
        assert entry["mark"] == ("saturated" if entry["split"] == "train" else None)


@pytest.mark.ac("gh-656:AC-1")
def test_a_case_marks_saturated_on_exactly_1_and_suspect_zero_on_exactly_0() -> None:
    one, zero, mixed, near = _case("one"), _case("zero"), _case("mixed", "validation"), _case("near")
    found = (_found(one, _item(1.0)) | _found(zero, _item(0.0)) | _found(near, _item(0.99))
             | _found(mixed, _item(1.0), _item(0.0)))
    entries = cast(dict[str, dict[str, object]], seed_signals([one, zero, mixed, near], found, {}, set(), False)["cases"])
    assert {name: entry["mark"] for name, entry in entries.items()} == {
        "one": "saturated", "zero": "suspect-zero", "mixed": None, "near": None}
    assert entries["mixed"]["scores"] == [1.0, 0.0]


@pytest.mark.ac("gh-656:AC-2")
@pytest.mark.parametrize("extra", [{"gate_fault": "timeout"}, {"gate_fault": "invalid-output"},
                                   {"status": "activation-rejected"}, {"status": "capture-failed"}])
def test_a_seed_that_scores_0_through_a_setup_fault_stops_before_the_search_and_a_resume_refuses_it(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch, extra: dict[str, object]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _zero_seed(state)
    state.extra = extra
    grants = _grants(monkeypatch, out)
    with pytest.raises(CodedError) as caught:
        _ = _go(state, target, out, case_hash, calls)
    assert caught.value.code == "cases-unscorable"
    names = [str(case["id"]) for case in _cases(out, "train", "validation")]
    assert all(name in str(caught.value) for name in names) and str(next(iter(extra.values()))) in str(caught.value)
    assert _headroom(out)["verdict"] == "unscorable" and grants == [] and state.proposals == 0
    seen = (len(state.search_log), state.invokes)
    with pytest.raises(CodedError) as again:
        _ = _go(state, target, out, case_hash, calls)
    assert again.value.code == "cases-unscorable" and str(again.value) == str(caught.value)
    assert (len(state.search_log), state.invokes) == seen


@pytest.mark.ac("gh-656:AC-2")
def test_a_case_stops_only_when_every_repeat_has_a_setup_fault() -> None:
    case = _case("v", "validation")
    both = seed_signals([case], _found(case, _item(0.0, fault="timeout"), _item(0.0, status="capture-failed")), {}, set(), True)
    assert [item["case_id"] for item in cast(list[dict[str, object]], both["unscorable"])] == ["v"]
    assert both["warnings"] == []


@pytest.mark.ac("gh-656:AC-2")
def test_the_unscorable_cause_names_the_first_diagnostic_cut_to_200_characters() -> None:
    case = _case("v", "validation")
    first = _item(0.0, fault="timeout") | {"gate_diagnostic": "  the grader argv exits with code 124\n" + "d" * 400}
    second = _item(0.0, fault="timeout") | {"gate_diagnostic": "other text"}
    unscorable = cast(list[dict[str, object]], seed_signals([case], _found(case, first, second), {}, set(), True)["unscorable"])
    shown = ("the grader argv exits with code 124\n" + "d" * 400)[:200]
    assert unscorable[0]["cause"] == f"setup fault timeout ({shown})"


@pytest.mark.ac("gh-656:AC-3")
def test_a_seed_that_scores_0_without_a_setup_fault_warns_and_the_run_continues(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _zero_seed(state)
    assert _go(state, target, out, case_hash, calls)["phase"] == "complete"
    stored = cast(list[str], _headroom(out)["warnings"])
    suspect = [text for text in stored if text.startswith("suspect-zero")]
    names = [str(case["id"]) for case in _cases(out, "train", "validation")]
    assert len(suspect) == len(names) and all(sum(f"of {name} (" in text for text in suspect) == 1 for name in names)
    assert all("scored 0" in text for text in suspect) and _headroom(out)["verdict"] == "ok"
    printed = capsys.readouterr().err
    assert all(f"skillz-experiment: warning: {text}" in printed for text in stored)


@pytest.mark.ac("gh-656:AC-3")
@pytest.mark.parametrize("second", [_item(0.0), _item(0.0, fault="timeout")])
def test_one_repeat_without_a_setup_fault_makes_the_zero_a_warning_that_names_the_fault(second: dict[str, object]) -> None:
    case = _case("v", "validation")
    signals = seed_signals([case], _found(case, _item(0.0, fault="timeout"), second), {}, set(), True)
    warned = second == _item(0.0)
    assert (signals["unscorable"] == []) is warned and bool(signals["warnings"]) is warned
    if warned:
        assert "v" in cast(list[str], signals["warnings"])[0] and "timeout" in cast(list[str], signals["warnings"])[0]


@pytest.mark.ac("gh-656:AC-4")
def test_a_fail_to_pass_regression_case_is_never_suspect_zero_and_a_plain_case_is() -> None:
    regression, plain = _case("r"), _case("p")
    found = _found(regression, _item(0.0)) | _found(plain, _item(0.0))
    signals = seed_signals([regression, plain], found, {"r": {"state": "fail-to-pass"}}, set(), True)
    entries = cast(dict[str, dict[str, object]], signals["cases"])
    assert entries["r"]["mark"] is None and entries["p"]["mark"] == "suspect-zero"
    warnings = cast(list[str], signals["warnings"])
    assert len(warnings) == 1 and "of p (" in warnings[0] and signals["unscorable"] == []


@pytest.mark.ac("gh-656:AC-2")
def test_a_setup_fault_on_every_repeat_of_a_fail_to_pass_case_still_stops() -> None:
    regression = _case("r", "validation")
    found = _found(regression, _item(0.0, fault="timeout"), _item(0.0, status="capture-failed"))
    signals = seed_signals([regression], found, {"r": {"state": "fail-to-pass"}}, set(), True)
    assert cast(dict[str, dict[str, object]], signals["cases"])["r"]["mark"] is None and signals["warnings"] == []
    unscorable = cast(list[dict[str, object]], signals["unscorable"])
    assert [item["case_id"] for item in unscorable] == ["r"]
    assert unscorable[0]["cause"] == "setup fault capture-failed, timeout"


@pytest.mark.ac("gh-658:AC-1")
def test_fewer_than_two_train_cases_with_headroom_warn_before_the_search(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _saturate_train(state)
    with pytest.raises(CodedError):
        _ = _go(state, target, out, case_hash, calls)
    train = len(_cases(out, "train"))
    stored = [text for text in cast(list[str], _headroom(out)["warnings"]) if text.startswith("saturation")]
    assert len(stored) == 1 and f"0 of {train} train cases" in stored[0]
    assert f"skillz-experiment: warning: {stored[0]}" in capsys.readouterr().err


@pytest.mark.ac("gh-658:AC-1")
def test_the_saturation_warning_needs_fewer_than_two_open_train_cases_and_skips_a_min_token_saving_run() -> None:
    saturated, open_a, open_b, floor = _case("s"), _case("a"), _case("b"), _case("f")
    found = (_found(saturated, _item(1.0)) | _found(open_a, _item(0.5)) | _found(open_b, _item(0.5))
             | _found(floor, _item(1.0)))
    two = seed_signals([saturated, open_a, open_b], found, {}, set(), False)
    assert two["warnings"] == []
    one = seed_signals([saturated, open_a, floor], found, {"f": {"state": "pass-to-pass"}}, {"f"}, False)
    assert len(cast(list[str], one["warnings"])) == 1 and "1 of 3 train cases" in cast(list[str], one["warnings"])[0]
    assert seed_signals([saturated, open_a, floor], found, {}, {"f"}, True)["warnings"] == []


@pytest.mark.ac("gh-658:AC-1")
def test_a_suspect_zero_train_case_has_headroom_and_no_saturation_warning_follows() -> None:
    cases = [_case("x"), _case("y"), _case("z")]
    found = _found(cases[0], _item(0.0)) | _found(cases[1], _item(0.0)) | _found(cases[2], _item(0.0))
    warnings = cast(list[str], seed_signals(cases, found, {}, set(), False)["warnings"])
    assert len(warnings) == 3 and all(text.startswith("suspect-zero") for text in warnings)


@pytest.mark.ac("gh-658:AC-1")
def test_a_train_case_with_no_scored_outcome_is_not_headroom() -> None:
    saturated, unscored_a, unscored_b = _case("s"), _case("a"), _case("b")
    found = _found(saturated, _item(1.0))
    warnings = cast(list[str], seed_signals([saturated, unscored_a, unscored_b], found, {}, set(), False)["warnings"])
    assert len(warnings) == 1 and "0 of 3 train cases" in warnings[0]

@pytest.mark.ac("gh-658:AC-2")
def test_the_saturated_counts_per_family_and_split_reach_the_export_with_the_warnings(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    statistics = {"min_token_saving": 0.3, "max_quality_loss": 0.5}
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out, statistics=statistics)
    state = Search(target)
    saturated_train = [str(case["id"]) for case in _cases(out, "train")][:1]

    def seed_score(text: str, split: str, case: str) -> float | None:
        if "improved" in text:
            return None
        return 1.0 if case in saturated_train else 0.0 if split == "validation" else None

    state.case_score_of = seed_score
    result = _go(state, target, out, case_hash, calls, statistics)
    assert result["phase"] == "complete"
    headroom = _headroom(out)
    train = _cases(out, "train")
    first = next(case for case in train if str(case["id"]) == saturated_train[0])
    assert headroom["saturated"]["splits"] == {"train": 1, "validation": 0}
    assert headroom["saturated"]["families"][first["family"]] == 1
    assert any(count == 0 for count in headroom["saturated"]["families"].values())  # pyright: ignore[reportAny]
    _ = export(out, tmp_path / "exported")
    shown = cast(dict[str, Any], read(tmp_path / "exported" / "report.json")["headroom"])  # pyright: ignore[reportExplicitAny]
    assert shown["saturated"] == headroom["saturated"] and shown["warnings"] == headroom["warnings"]
    assert shown["warnings"] and shown["cases"] == headroom["cases"]


@pytest.mark.ac("gh-656:AC-1")
def test_a_resumed_run_keeps_a_stored_block_that_lacks_the_new_fields(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    _saturate_train(state)
    with pytest.raises(CodedError):
        _ = _go(state, target, out, case_hash, calls)
    old = {key: value for key, value in _headroom(out).items() if key in ("ceiling", "families")} | {"verdict": "ok"}  # pyright: ignore[reportAny]
    record = read(out / "run.json")
    record["headroom"] = old
    write(out / "run.json", record)
    state.score_of = None
    _ = _go(state, target, out, case_hash, calls)
    assert _headroom(out) == old
