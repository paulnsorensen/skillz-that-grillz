"""gh-651: a regression case shows whether the seed reproduces the field failure it came from."""
# pyright: reportAny=false
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments._cases import Case, CodedError
from skillz_experiments._evals_format import parse_journeys
from skillz_experiments._intake import DRAFT_NAME, case_hash, load_draft, split_cases
from skillz_experiments._records import read
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import Stop, _session, run
from skillz_experiments.run._plan import VALIDATION_REPEATS
from skillz_experiments.run._reproduce import REPRODUCE_ARM, floor_cases, reproductions

from test_run_search import MODEL, Search

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
Started = tuple[Path, Path, Search, str, int]


def _case(**extra: object) -> dict[str, object]:
    return {"id": "field-1", "family": "f", "kind": "task", "request": "Echo the file.", "files": {"fixture.md": "hi\n"},
            "expected": {"criteria": [{"id": "answer", "text": "THE answer SHALL echo the file."}]},
            "source": "skill"} | extra


def _draft(tmp_path: Path, *cases: dict[str, object]) -> Path:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(list(cases)), encoding="utf-8")
    return path


def _start(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
           regression: Callable[[dict[str, object]], bool] = lambda _case: True) -> Started:
    """Draft the default 6 families of 2 cases, mark the chosen cases as session regression cases, and approve."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=6)
    path = out / DRAFT_NAME
    cases = cast(list[dict[str, object]], json.loads(path.read_text(encoding="utf-8")))
    for case in cases:
        if regression(case):
            case |= {"regression": True, "source": "session"}
    _ = path.write_text(json.dumps(cases), encoding="utf-8")
    approved, calls = approvals(target, out)
    return target, out, Search(target), approved, calls


def _go(state: Search, target: Path, out: Path, approved: str, calls: int) -> dict[str, object]:
    return run(target, out, MODEL, live=True, approve_cases=approved, approve_budget=calls, factory=state.factory)


def _record(out: Path) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, Any], read(out / "run.json"))  # pyright: ignore[reportExplicitAny]


def _reproduction(out: Path) -> dict[str, dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, dict[str, Any]], _record(out)["headroom"]["reproduction"])  # pyright: ignore[reportExplicitAny]


def _splits(out: Path, *names: str) -> dict[str, str]:
    cases = cast(list[dict[str, str]], read(out / "cases.json")["cases"])
    return {case["id"]: case["split"] for case in cases if case["split"] in names}


def _seed_zero(state: Search) -> None:
    """Score the seed 0 on every split. Improved texts keep the default score."""
    state.score_of = lambda text, _split: 0.0 if "improved" not in text else None


def _seed_one_on_train(state: Search) -> None:
    state.score_of = lambda text, split: 1.0 if split == "train" and "improved" not in text else None


@pytest.mark.ac("gh-651:AC-1")
def test_a_session_case_accepts_regression_true_in_the_draft_and_the_journeys_file(tmp_path: Path) -> None:
    cases = load_draft(_draft(tmp_path, _case(source="session", regression=True)))
    assert [case.regression for case in cases] == [True]
    journey = {"id": "j1", "kind": "journey", "request": "Start.", "brief": "You want x.", "expected": {"must": ["x"]},
               "source": "session", "regression": True}
    parsed = parse_journeys([journey], strict=False)
    assert parsed.errors == () and parsed.cases[0]["regression"] is True


@pytest.mark.ac("gh-651:AC-1")
@pytest.mark.parametrize(("regression", "source", "reason"), [
    ("yes", "session", "true or false"), (1, "session", "true or false"), (None, "session", "true or false"),
    (True, "skill", "needs source: session")])
def test_a_bad_regression_value_stops_as_cases_invalid_naming_the_case(
        tmp_path: Path, regression: object, source: str, reason: str) -> None:
    with pytest.raises(CodedError) as draft:
        _ = load_draft(_draft(tmp_path, _case(source=source, regression=regression)))
    assert draft.value.code == "cases-invalid" and "field-1" in str(draft.value) and reason in str(draft.value)
    journey = {"id": "j1", "kind": "journey", "request": "Start.", "brief": "You want x.", "expected": {"must": ["x"]},
               "source": source, "regression": regression}
    parsed = parse_journeys([journey], strict=False)
    assert parsed.cases == () and len(parsed.errors) == 1 and "'j1'" in parsed.errors[0] and reason in parsed.errors[0]


@pytest.mark.ac("gh-651:AC-2")
def test_an_omitted_or_false_regression_gives_the_same_case_data_and_hash(tmp_path: Path) -> None:
    hashes: list[str] = []
    for extra in ({}, {"regression": False}):
        cases = [_case(id=f"c{index}", family=f"f{index}", **extra) for index in range(6)]
        drafted = split_cases(load_draft(_draft(tmp_path, *cases)), 7)
        assert all("regression" not in case.data() for case in drafted)
        hashes.append(case_hash(drafted, 7))
    assert hashes[0] == hashes[1]
    cases = [_case(id=f"c{index}", family=f"f{index}", **({"source": "session", "regression": True} if index == 0 else {}))
             for index in range(6)]
    marked = split_cases(load_draft(_draft(tmp_path, *cases)), 7)
    assert [case.data().get("regression") for case in marked] == [True, None, None, None, None, None]


@pytest.mark.ac("gh-651:AC-3")
def test_a_train_or_validation_regression_case_records_its_seed_scores_and_reproduces_when_all_are_zero(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = _start(tmp_path, make_target, write_draft, approvals)
    _seed_zero(state)
    _ = _go(state, target, out, approved, calls)
    found = _reproduction(out)
    scored = _splits(out, "train", "validation")
    assert scored and {name for name, item in found.items() if item["split"] != "holdout"} == set(scored)
    for name, split in scored.items():
        item = found[name]
        assert item["split"] == split and item["solvable"] == "unknown" and item["reproduces"] is True
        assert item["scores"] == [0.0] * (len(item["scores"])) and len(item["scores"]) == (VALIDATION_REPEATS if split == "validation" else 1)
        assert isinstance(item["unmet"], dict)


@pytest.mark.ac("gh-651:AC-3")
def test_a_regression_case_that_the_seed_passes_on_one_repeat_does_not_reproduce(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = _start(tmp_path, make_target, write_draft, approvals)
    passed: list[int] = []

    def partial(text: str, split: str) -> float | None:
        """Score the first validation seed call 1.0 and every other seed call 0."""
        if "improved" in text:
            return None
        if split == "validation" and not passed:
            passed.append(1)
            return 1.0
        return 0.0

    state.score_of = partial
    _ = _go(state, target, out, approved, calls)
    found = _reproduction(out)
    validation = _splits(out, "validation")
    floor = [name for name in validation if found[name]["reproduces"] is False]
    assert len(floor) == 1 and 1.0 in found[floor[0]]["scores"] and 0.0 in found[floor[0]]["scores"]
    assert all(item["reproduces"] is True for name, item in found.items() if name != floor[0])


@pytest.mark.ac("gh-651:AC-3")
def test_a_regression_case_with_no_scored_outcome_records_null_with_a_reason() -> None:
    case = Case("r", "f", "train", "request", {"a": "b"}, {}, "session", True, regression=True)
    plain = Case("p", "f", "train", "request", {"a": "b"}, {}, "skill", True)
    found = reproductions([case, plain], {}, {})
    assert found == {"r": {"split": "train", "solvable": "unknown", "scores": [], "reproduces": None,
                           "reason": "no scored seed outcome"}}
    assert floor_cases(found) == set()


@pytest.mark.ac("gh-651:AC-4")
@pytest.mark.ac("gh-651:AC-9")
def test_each_holdout_regression_case_scores_once_under_the_reproduce_arm_before_the_engine_starts(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state, approved, calls = _start(tmp_path, make_target, write_draft, approvals)
    _seed_zero(state)
    before_engine: list[int] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportExplicitAny]
        before_engine.append(state.holdout_calls)
        return real(*args, **kwargs)

    monkeypatch.setattr(_session, "pareto_search", spy)
    result = _go(state, target, out, approved, calls)
    holdout = _splits(out, "holdout")
    assert result["phase"] == "complete" and holdout
    assert before_engine == [len(holdout)]
    outcomes = cast(list[dict[str, Any]], _record(out)["outcomes"])  # pyright: ignore[reportExplicitAny]
    reproduced = [item for item in outcomes if item.get("arm") == REPRODUCE_ARM]
    assert Counter(item["case_id"] for item in reproduced) == Counter(dict.fromkeys(holdout, 1))
    assert {item["split"] for item in reproduced} == {"holdout"} and not any("search_candidate" in item for item in reproduced)
    assert {name for name, item in _reproduction(out).items() if item["split"] == "holdout"} == set(holdout)
    assert result["headroom"] == _record(out)["headroom"]
    # The gate scores the holdout on its own arms: the reproduce arm adds nothing to them.
    assert [item for item in outcomes if item.get("split") == "holdout" and item.get("arm") != REPRODUCE_ARM]


@pytest.mark.ac("gh-651:AC-9")
def test_a_holdout_regression_case_keeps_only_the_level_of_each_unmet_criterion() -> None:
    outcome = {"score": 0.0, "criteria": {"gap": {"level": 2, "reason": "the draft quotes the holdout text", "met": False},
                                          "ok": {"level": 4, "reason": "fine", "met": True}}}
    held = Case("h", "f", "holdout", "request", {"a": "b"}, {}, "session", True, regression=True)
    trained = Case("t", "g", "train", "request", {"a": "b"}, {}, "session", True, regression=True)
    found = reproductions([held, trained], {("train", "t", None): outcome}, {"h": outcome})
    assert found["h"]["unmet"] == {"gap": {"level": 2}} and found["h"]["reproduces"] is True
    assert found["t"]["unmet"] == {"gap": {"level": 2, "reason": "the draft quotes the holdout text"}}


@pytest.mark.ac("gh-651:AC-4")
def test_a_resume_does_not_score_a_holdout_regression_case_again(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = _start(tmp_path, make_target, write_draft, approvals)
    _seed_zero(state)
    state.holdout_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == 4 else None)
    with pytest.raises(EnvironmentDiffers):
        _ = _go(state, target, out, approved, calls)
    assert "headroom" not in _record(out)
    done = [item for item in _record(out)["outcomes"] if item.get("arm") == REPRODUCE_ARM]
    assert 0 < len(done) < len(_splits(out, "holdout"))
    state.holdout_fault = lambda _number: None
    _ = run(target, out, MODEL, live=True, factory=state.factory)
    again = Counter(item["case_id"] for item in _record(out)["outcomes"] if item.get("arm") == REPRODUCE_ARM)
    assert again == Counter(dict.fromkeys(_splits(out, "holdout"), 1))


@pytest.mark.ac("gh-651:AC-5")
def test_a_family_with_only_floor_cases_does_not_stop_the_run_and_the_floor_stays_listed(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = _start(tmp_path, make_target, write_draft, approvals)
    _seed_one_on_train(state)
    _ = _go(state, target, out, approved, calls)
    headroom = _record(out)["headroom"]
    train = _splits(out, "train")
    assert headroom["verdict"] == "ok"
    assert all(headroom["reproduction"][name]["reproduces"] is False for name in train)
    assert not {item["split"] for item in headroom["families"].values()} & {"train"}


@pytest.mark.ac("gh-651:AC-5")
@pytest.mark.ac("gh-651:AC-7")
def test_a_floor_case_stays_out_of_its_family_mean_and_a_too_easy_stop_carries_the_reproduction(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, approved, calls = _start(
        tmp_path, make_target, write_draft, approvals, regression=lambda case: str(case["id"]).endswith("-0"))
    _seed_one_on_train(state)
    with pytest.raises(Stop) as caught:
        _ = _go(state, target, out, approved, calls)
    assert caught.value.code == "cases-too-easy"
    headroom = _record(out)["headroom"]
    train = _splits(out, "train")
    floors = {name for name in train if name.endswith("-0")}
    assert floors and {name for name, item in headroom["reproduction"].items() if item["split"] == "train"} == floors
    # Each train family has one floor case and one other case: only the other case adds to the mean.
    assert {item["cases"] for item in headroom["families"].values() if item["split"] == "train"} == {1}
    baseline = cast(dict[str, Any], caught.value.data["baseline"])  # pyright: ignore[reportExplicitAny]
    assert baseline["headroom"]["reproduction"] == headroom["reproduction"]


@pytest.mark.ac("gh-651:AC-6")
def test_holdout_regression_cases_add_their_calls_to_the_approved_total_and_show_on_their_own_line(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    plain = _start(tmp_path / "plain", make_target, write_draft, approvals, regression=lambda _case: False)
    marked = _start(tmp_path / "marked", make_target, write_draft, approvals)
    estimates: list[Mapping[str, object]] = []
    for target, out, state, approved, _calls in (plain, marked):
        with pytest.raises(Stop) as budget:
            _ = run(target, out, MODEL, live=True, approve_cases=approved, factory=state.factory)
        estimates.append(cast(Mapping[str, object], budget.value.data["estimate"]))
    holdout = len(_splits(marked[1], "holdout"))
    per_case = marked[2].contract.calls("echo")
    assert "reproduce" not in estimates[0]
    assert estimates[1]["reproduce"] == {"calls": holdout * per_case}
    assert cast(int, estimates[1]["calls"]) - cast(int, estimates[0]["calls"]) == holdout * per_case
    assert marked[4] - plain[4] == holdout * per_case
