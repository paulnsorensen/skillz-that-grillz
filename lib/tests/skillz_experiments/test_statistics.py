"""Statistics thresholds: the gate floors, the contract block, and run-time resolution and freeze."""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._cli import main
from skillz_experiments._contract import _LEGACY_SKILLZ, parse  # pyright: ignore[reportPrivateUsage]
from skillz_experiments._gate import Simulation, Statistics, simulate, verdict
from skillz_experiments._records import read
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import Stop, export, resolve_statistics, run

MODEL = "local-test"
LEGACY_IDENTITY = "911fc7bbdaf308d1e15d25fdde28d63c3b85509e3e3c42ba37eb49cb42aff7b6"
ContractDocument = dict[str, object]

pytestmark = pytest.mark.usefixtures("host_login")


# --- gate ------------------------------------------------------------------------------------------------

def test_simulate_default_rates_are_pinned() -> None:
    assert simulate() == Simulation(trials=2000, cases=10, rate_2se=0.0365, rate_one_holdout=0.8115)


@pytest.mark.parametrize("values", [
    [0.1, 0.2, 0.3], [-0.1, -0.2, -0.3], [-0.1, 0.1, 0.3], [3.0, 1.0], [-3.0, -1.0], [0.1, 0.1, 0.1],
    [-0.1, -0.1], [0.0, 0.0, 0.0], [0.5], [], [math.nan, 1.0], [math.inf, 0.0, 0.0]])
def test_default_thresholds_return_the_unchanged_verdict(values: list[float]) -> None:
    plain, explicit = verdict(values), verdict(values, thresholds=Statistics())
    assert plain.reasons == ()
    assert (explicit.verdict, explicit.cases, explicit.reasons) == (plain.verdict, plain.cases, ())
    assert str(explicit.delta) == str(plain.delta) and str(explicit.se) == str(plain.se)


# Literal results of the gate before the thresholds change: the edge cases keep their verdict, delta, and SE.
@pytest.mark.parametrize(("values", "name", "delta", "se", "cases"), [
    ([0.1, 0.1, 0.1], "promote", 0.10000000000000002, 0.0, 3),
    ([-0.1, -0.1], "reject", -0.1, 0.0, 2),
    ([0.0, 0.0, 0.0], "inconclusive", 0.0, 0.0, 3),
    ([0.5], "inconclusive", 0.5, 0.0, 1),
    ([], "inconclusive", 0.0, 0.0, 0)])
def test_the_gate_edge_cases_keep_their_pinned_result(
        values: list[float], name: str, delta: float, se: float, cases: int) -> None:
    result = verdict(values)
    assert (result.verdict, result.delta, result.se, result.cases, result.reasons) == (name, delta, se, cases, ())


@pytest.mark.parametrize("values", [[math.nan, 1.0], [math.inf, 0.0, 0.0]])
def test_a_non_finite_delta_is_inconclusive_with_nan_delta_and_se(values: list[float]) -> None:
    result = verdict(values)
    assert result.verdict == "inconclusive" and result.cases == len(values)
    assert math.isnan(result.delta) and math.isnan(result.se)


# Deltas 1, 2, 3: mean 2.0, se 1/sqrt(3), so 2*se is about 1.1547.
SPREAD = [1.0, 2.0, 3.0]


def test_min_gain_equal_to_the_delta_still_promotes() -> None:
    assert verdict(SPREAD, thresholds=Statistics(min_gain=2.0)).verdict == "promote"


def test_min_gain_above_the_delta_turns_a_promotion_inconclusive() -> None:
    result = verdict(SPREAD, thresholds=Statistics(min_gain=2.5))
    assert (result.verdict, result.reasons) == ("inconclusive", ("min-gain",))
    assert result.delta == 2.0 and result.cases == 3


def test_lower_bound_needs_the_delta_to_exceed_two_se_plus_the_floor() -> None:
    assert verdict(SPREAD, thresholds=Statistics(min_lower_bound=0.8)).verdict == "promote"
    result = verdict(SPREAD, thresholds=Statistics(min_lower_bound=0.9))
    assert (result.verdict, result.reasons) == ("inconclusive", ("lower-bound",))


def test_both_floors_report_both_reasons_in_order() -> None:
    result = verdict(SPREAD, thresholds=Statistics(min_gain=2.5, min_lower_bound=0.9))
    assert (result.verdict, result.reasons) == ("inconclusive", ("min-gain", "lower-bound"))


def test_the_floors_apply_to_a_zero_se_promotion_with_two_se_as_zero() -> None:
    same = [0.5, 0.5, 0.5]
    assert verdict(same, thresholds=Statistics(min_lower_bound=0.25)).verdict == "promote"
    at_bound = verdict(same, thresholds=Statistics(min_lower_bound=0.5))
    assert (at_bound.verdict, at_bound.reasons) == ("inconclusive", ("lower-bound",))
    low = verdict(same, thresholds=Statistics(min_gain=0.75))
    assert (low.verdict, low.reasons) == ("inconclusive", ("min-gain",))


@pytest.mark.parametrize("values", [[-1.0, -2.0, -3.0], [-0.1, 0.1, 0.3], [0.5], [], [math.nan, 1.0]])
def test_the_floors_never_change_a_verdict_that_is_not_a_promotion(values: list[float]) -> None:
    tight = Statistics(min_gain=9.0, min_lower_bound=9.0)
    plain, floored = verdict(values), verdict(values, thresholds=tight)
    assert (floored.verdict, floored.cases, floored.reasons) == (plain.verdict, plain.cases, ())


# --- contract --------------------------------------------------------------------------------------------

def _document(**extra: object) -> ContractDocument:
    return {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": {"echo": {"grader": "exact-json"}}, "editable": [], **extra}


def test_a_contract_without_statistics_keeps_its_identity_hash() -> None:
    assert _LEGACY_SKILLZ.identity == LEGACY_IDENTITY
    contract = parse(_document(), "skill")
    assert contract.statistics is None and "statistics" not in contract.data()
    assert parse(contract.data(), "skill").identity == contract.identity


def test_a_statistics_block_enters_the_identity_hash_and_round_trips() -> None:
    plain = parse(_document(), "skill")
    contract = parse(_document(statistics={"min_gain": 1, "min_lower_bound": 0.25}), "skill")
    assert contract.statistics == {"min_gain": 1.0, "min_lower_bound": 0.25}
    assert contract.data()["statistics"] == {"min_gain": 1.0, "min_lower_bound": 0.25}
    assert contract.identity != plain.identity
    assert parse(contract.data(), "skill").identity == contract.identity


@pytest.mark.parametrize("block", [
    {"family_budget": 0.1}, {"min_gain": 0.1, "surprise": 1}, {"min_gain": -0.1}, {"min_lower_bound": -1},
    {"min_gain": "0.1"}, {"min_gain": True}, {"min_lower_bound": None}, {"min_gain": [0.1]},
    {"min_gain": math.nan}, {"min_gain": math.inf}, [], "0.1", None])
def test_an_invalid_statistics_block_fails_parse_with_a_code(block: object) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics=block), "skill")
    assert caught.value.code == "contract-statistics-invalid"


# --- resolution, freeze, and resume ----------------------------------------------------------------------

def test_resolution_takes_the_flag_then_the_contract_then_the_default() -> None:
    contract = parse(_document(statistics={"min_gain": 0.4, "min_lower_bound": 0.2}), "skill")
    assert resolve_statistics(contract, {}) == Statistics(min_gain=0.4, min_lower_bound=0.2)
    assert resolve_statistics(contract, {"min_gain": 0.1}) == Statistics(min_gain=0.1, min_lower_bound=0.2)
    assert resolve_statistics(contract, {"min_lower_bound": 0.0}) == Statistics(min_gain=0.4, min_lower_bound=0.0)
    assert resolve_statistics(parse(_document(), "skill"), {}) == Statistics()
    assert resolve_statistics(parse(_document(statistics={"min_gain": 0.4}), "skill"), {}) == Statistics(min_gain=0.4)


@final
class _Provider:
    """A fake provider. The seed scores 0 and a proposal that says `improved` scores 1."""

    def __init__(self, _model: str, _budget: Budget, _checkpoint: Callable[[], None]) -> None:
        pass

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        del holdout
        return {"score": float("improved" in candidate.files["SKILL.md"]), "candidate_hash": candidate.identity,
                "case_hash": case.identifier}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout
        assert schema is not None
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def _declare(target: Path, **block: float) -> None:
    path = target / "evals/autoimprove.json"
    document = cast(ContractDocument, json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"statistics": block}))


def test_a_contract_floor_is_frozen_and_the_gate_reports_its_reason(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _declare(target, min_gain=2.0)
    result = approved_run(target, out, factory=_Provider)
    assert read(out / "run.json")["statistics"] == {"min_gain": 2.0, "min_lower_bound": 0.0}
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["delta"], gate["reasons"]) == ("inconclusive", 1.0, ["min-gain"])
    destination = tmp_path / "export"
    _ = export(out, destination)
    report = cast(dict[str, object], json.loads((destination / "report.json").read_text()))
    assert cast(dict[str, object], report["gate"])["reasons"] == ["min-gain"]


def test_a_flag_below_the_contract_value_wins_and_the_default_gate_has_no_reasons(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _declare(target, min_gain=2.0, min_lower_bound=0.5)
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=_Provider,
                 statistics={"min_gain": 0.5, "min_lower_bound": 0.0})
    assert read(out / "run.json")["statistics"] == {"min_gain": 0.5, "min_lower_bound": 0.0}
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["reasons"]) == ("promote", [])


def test_a_resume_with_a_different_statistics_flag_stops_with_run_config_differs(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics={"min_gain": 0.5})
    assert prepared.value.code == "live-required"
    with pytest.raises(CodedError) as gain:
        _ = run(target, out, MODEL, statistics={"min_gain": 0.25})
    with pytest.raises(CodedError) as bound:
        _ = run(target, out, MODEL, statistics={"min_lower_bound": 0.1})
    assert gain.value.code == bound.value.code == "run-config-differs"
    assert "--min-gain" in str(gain.value) and "--min-lower-bound" in str(bound.value)
    with pytest.raises(Stop) as same:
        _ = run(target, out, MODEL, statistics={"min_gain": 0.5, "min_lower_bound": 0.0})
    assert same.value.code == "live-required"
    with pytest.raises(Stop) as none:
        _ = run(target, out, MODEL)
    assert none.value.code == "live-required"


def test_approval_stops_echo_the_thresholds_and_a_dropped_flag_falls_back_to_the_default(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    flagged = {"min_gain": 0.5, "min_lower_bound": 0.0}
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, statistics={"min_gain": 0.5})
    assert cases.value.code == "cases-unapproved" and cases.value.data["statistics"] == flagged
    case_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, approve_cases=case_hash, statistics={"min_gain": 0.5})
    assert budget.value.code == "budget-unapproved" and budget.value.data["statistics"] == flagged
    with pytest.raises(Stop) as dropped:
        _ = run(target, out, MODEL, approve_cases=case_hash)
    assert dropped.value.code == "budget-unapproved" and dropped.value.data["statistics"] == Statistics().data()


@pytest.mark.parametrize("name", ["min_gain", "min_lower_bound"])
@pytest.mark.parametrize("value", [-0.1, math.nan, math.inf])
def test_run_refuses_an_invalid_statistics_flag(
        tmp_path: Path, make_target: Callable[..., Path], name: str, value: float) -> None:
    flag = "--" + name.replace("_", "-")
    with pytest.raises(ValueError, match=f"{flag} must be a finite number of at least 0"):
        _ = run(make_target(tmp_path), tmp_path / "run", MODEL, statistics={name: value})


def test_the_cli_passes_the_statistics_flags_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(*_arguments: object, **options: object) -> dict[str, object]:
        seen.update(options)
        return {}

    monkeypatch.setattr("skillz_experiments._cli.run_workflow", fake)
    base = ["run", "--target", str(tmp_path), "--out", str(tmp_path / "run"), "--model", MODEL]
    assert main([*base, "--min-gain", "0.05", "--min-lower-bound", "0.02"]) == 0
    assert seen["statistics"] == {"min_gain": 0.05, "min_lower_bound": 0.02}
    assert main([*base, "--min-gain", "0.05"]) == 0
    assert seen["statistics"] == {"min_gain": 0.05}
    assert main(base) == 0
    assert seen["statistics"] is None


def test_a_negative_zero_or_int_flag_is_frozen_and_echoed_as_a_plain_float(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    given = {"min_gain": -0.0, "min_lower_bound": 1}
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, statistics=given)
    assert json.dumps(cases.value.data["statistics"]) == '{"min_gain": 0.0, "min_lower_bound": 1.0}'
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics=given)
    assert prepared.value.code == "live-required"
    assert json.dumps(read(out / "run.json")["statistics"]) == '{"min_gain": 0.0, "min_lower_bound": 1.0}'


def test_run_refuses_an_unknown_statistics_flag_name_and_lists_the_allowed_flags(
        tmp_path: Path, make_target: Callable[..., Path]) -> None:
    with pytest.raises(ValueError, match="unknown statistics flag: min_gian; allowed: --min-gain, --min-lower-bound"):
        _ = run(make_target(tmp_path), tmp_path / "run", MODEL, statistics={"min_gian": 0.5})
