"""The search objective, the candidate floor, and the order of the search approval steps (gh-757)."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from skillz_experiments._budget import JOURNEY_CALLS
from skillz_experiments._cases import CodedError
from skillz_experiments._cli import main
from skillz_experiments._records import read
from skillz_experiments.harness import EnvironmentDiffers
from skillz_experiments.run import Stop, _session, run
from skillz_experiments.run._continuation import PROSE_TOKENS_WARNING
from skillz_experiments.run._plan import CALLS_PER_REFLECTION, MINIMUM_CANDIDATES, VALIDATION_REPEATS
from skillz_experiments.search import search_allowance

from fake_provider import search_approval
from test_headroom import _saturate_train  # pyright: ignore[reportPrivateUsage]
from test_run_search import MODEL, Search

pytestmark = pytest.mark.usefixtures("host_login")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]
TOKENS = {"min_token_saving": 0.3, "max_quality_loss": 0.5}


def _record(out: Path) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    return cast(dict[str, Any], read(out / "run.json"))  # pyright: ignore[reportExplicitAny]


def _measured(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver, *, families: int = 6,
              per_family: int = 2, statistics: dict[str, float] | None = None,
              saturate: bool = False) -> tuple[Path, Path, Search]:
    """Measure a baseline with the fake provider. Return the target, the run directory, and the provider state."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=families, per_family=per_family)
    case_hash, calls = approvals(target, out, statistics=statistics)
    state = Search(target)
    if saturate:
        _saturate_train(state)
    baseline = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
                   statistics=statistics)
    assert baseline["search"] == "not-run" and _record(out)["plan_mode"] == "baseline"
    return target, out, state


def _engine_starts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record the keyword arguments of each GEPA engine start."""
    starts: list[dict[str, object]] = []
    real = _session.pareto_search  # pyright: ignore[reportPrivateLocalImportUsage]

    def spy(*args: Any, **kwargs: Any) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
        starts.append(kwargs)
        return real(*args, **kwargs)  # pyright: ignore[reportAny]

    monkeypatch.setattr(_session, "pareto_search", spy)
    return starts


def _stop(target: Path, out: Path, **options: Any) -> Stop:  # pyright: ignore[reportExplicitAny, reportAny]
    with pytest.raises(Stop) as stopped:
        _ = run(target, out, MODEL, search=True, **options)  # pyright: ignore[reportAny]
    return stopped.value


@pytest.mark.ac("gh-757:AC-1")
@pytest.mark.ac("gh-757:AC-13")
def test_a_search_call_without_an_objective_uses_strict_improvement_and_records_quality(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state = _measured(tmp_path, make_target, write_draft, approvals, statistics=TOKENS)
    starts = _engine_starts(monkeypatch)
    # An earlier call that stops records no objective, so its `--objective` does not decide the search.
    asked = _stop(target, out, objective="tokens")
    assert asked.code == "budget-unapproved" and asked.data["objective"] == "tokens"
    assert _record(out)["plan_mode"] == "baseline" and "objective" not in _record(out)
    result = run(target, out, MODEL, live=True, search=True, approve_budget=search_approval(target, out, MODEL),
                 factory=state.factory)
    assert result["phase"] == "complete"
    record = _record(out)
    assert record["objective"] == "quality" and record["statistics"]["min_token_saving"] == 0.3
    assert starts and all(start["cheaper"] is None for start in starts)


@pytest.mark.ac("gh-757:AC-2")
@pytest.mark.ac("gh-757:AC-13")
def test_the_token_objective_passes_gepa_the_cheaper_tie_and_the_approval_records_it(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state = _measured(tmp_path, make_target, write_draft, approvals, statistics=TOKENS)
    starts = _engine_starts(monkeypatch)
    approval = search_approval(target, out, MODEL, objective="tokens")
    result = run(target, out, MODEL, live=True, search=True, approve_budget=approval, objective="tokens",
                 factory=state.factory)
    assert result["phase"] == "complete" and _record(out)["objective"] == "tokens"
    assert starts and all(callable(start["cheaper"]) for start in starts)


@pytest.mark.ac("gh-757:AC-2")
def test_a_token_objective_without_min_token_saving_and_an_objective_without_search_stop_before_any_call(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _measured(tmp_path, make_target, write_draft, approvals)
    before = (state.invokes, len(state.search_log), (out / "run.json").read_bytes())
    with pytest.raises(ValueError, match="--objective tokens needs min_token_saving.*in a new run directory"):
        _ = run(target, out, MODEL, live=True, search=True, objective="tokens", factory=state.factory)
    with pytest.raises(ValueError, match="--objective applies only to a --search call"):
        _ = run(target, out, MODEL, live=True, objective="quality", factory=state.factory)
    with pytest.raises(ValueError, match="--objective must be quality or tokens"):
        _ = run(target, out, MODEL, live=True, search=True, objective="cost", factory=state.factory)
    assert (state.invokes, len(state.search_log), (out / "run.json").read_bytes()) == before


@pytest.mark.ac("gh-757:AC-2")
def test_the_cli_passes_the_objective_flag_to_run(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out, _state = _measured(tmp_path, make_target, write_draft, approvals)
    _ = capsys.readouterr()
    code = main(["run", "--target", str(target), "--out", str(out), "--model", MODEL, "--objective", "tokens"])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err.rstrip().rpartition("\n")[2]))
    assert code == 1 and "--objective applies only to a --search call" in str(error["error"])


@pytest.mark.ac("gh-757:AC-3")
@pytest.mark.ac("gh-757:AC-7")
@pytest.mark.ac("gh-757:AC-12")
def test_a_saturated_family_stops_the_quality_objective_and_the_token_objective_reaches_the_floor(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state = _measured(tmp_path, make_target, write_draft, approvals, families=12, statistics=TOKENS,
                                   saturate=True)
    assert MINIMUM_CANDIDATES == 6
    starts = _engine_starts(monkeypatch)
    for _ in range(2):
        with pytest.raises(CodedError) as quality:
            _ = run(target, out, MODEL, live=True, search=True, approve_budget=JOURNEY_CALLS, factory=state.factory)
        assert quality.value.code == "cases-too-easy"
    tokens = _stop(target, out, objective="tokens")
    assert tokens.code == "search-underpowered" and cast(int, tokens.data["expected_candidates"]) < MINIMUM_CANDIDATES
    assert starts == [] and state.invokes == 0 and _record(out)["plan_mode"] == "baseline"


@pytest.mark.ac("gh-757:AC-6")
@pytest.mark.ac("gh-757:AC-7")
def test_a_plan_under_six_candidates_stops_before_the_budget_question_and_names_the_smallest_approval(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state = _measured(tmp_path, make_target, write_draft, approvals, families=12)
    report = (out / "report.json").read_bytes()
    first = _stop(target, out)
    assert first.code == "search-underpowered"
    smallest = cast(int, first.data["smallest_approval"])
    assert cast(int, first.data["expected_candidates"]) < MINIMUM_CANDIDATES == first.data["minimum_candidates"]
    assert f"--approve-budget {smallest}" in str(first) and first.data["next"] == f"call run --search with --approve-budget {smallest}"
    # Each call applies the floor again, with or without an approval below the smallest number.
    for approval in (None, 200, smallest - 1):
        again = _stop(target, out, approve_budget=approval)
        assert (again.code, again.data["smallest_approval"]) == ("search-underpowered", smallest)
    assert (out / "report.json").read_bytes() == report and _record(out)["plan_mode"] == "baseline"
    assert state.invokes == 0
    passed = _stop(target, out, approve_budget=smallest)
    assert passed.code == "live-required"
    record = _record(out)
    assert record["plan_mode"] == "search" and record["estimate"]["expected_candidates"] >= MINIMUM_CANDIDATES


@pytest.mark.ac("gh-757:AC-6")
def test_a_plan_that_no_approval_up_to_the_raised_cap_limit_lifts_to_six_candidates_says_so(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _state = _measured(tmp_path, make_target, write_draft, approvals, families=60, per_family=3)
    for approval in (None, JOURNEY_CALLS):
        stopped = _stop(target, out, approve_budget=approval)
        assert stopped.code == "search-underpowered" and stopped.data["smallest_approval"] is None
        assert cast(int, stopped.data["expected_candidates"]) < MINIMUM_CANDIDATES
        assert stopped.data["next"] == (f"no approved number up to {JOURNEY_CALLS} calls buys {MINIMUM_CANDIDATES}; "
                                         + "lower --repeats, or draft fewer validation and holdout cases in a new run directory")
    assert (out / "report.json").is_file() and _record(out)["plan_mode"] == "baseline"


@pytest.mark.ac("gh-757:AC-5")
@pytest.mark.ac("gh-757:AC-7")
def test_the_search_estimate_shows_the_expected_candidates_and_the_gate_reserve(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, _state = _measured(tmp_path, make_target, write_draft, approvals)
    asked = _stop(target, out)
    assert asked.code == "budget-unapproved"
    estimate = cast(dict[str, int], asked.data["estimate"])
    cases = cast(list[dict[str, object]], read(out / "cases.json")["cases"])
    runs = VALIDATION_REPEATS * sum(case["split"] == "validation" for case in cases)
    expected = search_allowance(estimate["metric_calls"], runs) // (CALLS_PER_REFLECTION + runs)
    assert estimate["expected_candidates"] == expected >= MINIMUM_CANDIDATES
    assert estimate["reserve"] > estimate["replay_calls"] > 0
    assert _stop(target, out, approve_budget=estimate["calls"]).code == "live-required"
    assert _record(out)["budget"]["reserve"] == estimate["reserve"]


@pytest.mark.ac("gh-757:AC-4")
def test_a_resume_keeps_the_recorded_objective_and_refuses_another(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        monkeypatch: pytest.MonkeyPatch) -> None:
    target, out, state = _measured(tmp_path, make_target, write_draft, approvals, statistics=TOKENS)
    fault_at = state.search_calls + 3
    state.evaluate_fault = lambda number: (
        EnvironmentDiffers("runtime environment differs from the frozen record") if number == fault_at else None)
    approval = search_approval(target, out, MODEL, objective="tokens")
    with pytest.raises(EnvironmentDiffers):
        _ = run(target, out, MODEL, live=True, search=True, approve_budget=approval, objective="tokens",
                factory=state.factory)
    assert _record(out)["objective"] == "tokens" and _record(out)["phase"] == "prepared"
    before = (state.invokes, state.search_calls)
    with pytest.raises(CodedError) as refused:
        _ = run(target, out, MODEL, live=True, objective="quality", factory=state.factory)
    assert refused.value.code == "run-config-differs" and "--objective quality" in str(refused.value)
    assert (state.invokes, state.search_calls) == before
    starts = _engine_starts(monkeypatch)
    result = run(target, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete" and starts and all(callable(start["cheaper"]) for start in starts)


@pytest.mark.ac("gh-757:AC-10")
def test_a_token_search_with_no_edit_plans_prose_and_cli_on_a_target_with_a_helper(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out = make_target(tmp_path, helper=True), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out, statistics=TOKENS)
    state = Search(target)
    asked = _stop(target, out, live=True, objective="tokens", approve_cases=case_hash, approve_budget=calls,
                  statistics=TOKENS, factory=state.factory)
    assert asked.code == "budget-unapproved" and "objective_warning" not in asked.data
    assert _record(out)["edit"] == "prose+cli"


@pytest.mark.ac("gh-757:AC-10")
@pytest.mark.ac("gh-757:AC-11")
def test_a_token_search_that_falls_back_to_prose_states_why_and_warns_in_the_budget_stop(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
        capsys: pytest.CaptureFixture[str]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out, statistics=TOKENS)
    state = Search(target)
    asked = _stop(target, out, live=True, objective="tokens", approve_cases=case_hash, approve_budget=calls,
                  statistics=TOKENS, factory=state.factory)
    assert _record(out)["edit"] == "prose" and "this run uses --edit prose" in capsys.readouterr().err
    assert asked.code == "budget-unapproved" and asked.data["objective_warning"] == PROSE_TOKENS_WARNING
    assert "Markdown only" in PROSE_TOKENS_WARNING and "--edit prose+cli" in PROSE_TOKENS_WARNING
    assert "objective_warning" not in _stop(target, out).data


@pytest.mark.ac("gh-757:AC-11")
def test_a_token_search_with_a_named_prose_edit_warns_in_the_budget_stop(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out = make_target(tmp_path, helper=True), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out, statistics=TOKENS)
    state = Search(target)
    baseline = run(target, out, MODEL, live=True, edit="prose", approve_cases=case_hash, approve_budget=calls,
                   statistics=TOKENS, factory=state.factory)
    assert baseline["search"] == "not-run" and _record(out)["edit"] == "prose"
    asked = _stop(target, out, objective="tokens")
    assert asked.code == "budget-unapproved" and asked.data["objective_warning"] == PROSE_TOKENS_WARNING
