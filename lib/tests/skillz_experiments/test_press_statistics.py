"""Press pass on curd C1 (skillz-gate-statistics): adversarial tests for the statistics contract.

Tests marked FINDING exposed a contract defect that a Cook correction fixed.
"""
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
from skillz_experiments._contract import parse
from skillz_experiments._gate import Statistics, verdict
from skillz_experiments._records import read
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import Stop, resolve_statistics, run

MODEL = "local-test"
SPREAD = [1.0, 2.0, 3.0]

pytestmark = pytest.mark.usefixtures("host_login")


def _document(**extra: object) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": {"echo": {"grader": "exact-json"}}, "editable": [], **extra}


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


# --- contract parsing ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["max_token_budget", "token_saving"])
def test_parse_rejects_an_unknown_field(name: str) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics={name: 0.5}), "skill")
    assert caught.value.code == "contract-statistics-invalid"
    assert "unknown fields" in str(caught.value)


def test_parse_rejects_a_scalar_family_budgets() -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics={"family_budgets": 0.5}), "skill")
    assert caught.value.code == "contract-statistics-invalid"
    assert "object of numbers" in str(caught.value)


@pytest.mark.parametrize("big", [10**400, -(10**400)])
def test_parse_codes_an_integer_too_large_for_a_float(big: int) -> None:
    """FINDING: math.isfinite(10**400) raises OverflowError, so the block fails uncoded instead of coded."""
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics={"min_gain": big}), "skill")
    assert caught.value.code == "contract-statistics-invalid"


def test_parse_an_empty_block_hashes_like_an_omitted_block() -> None:
    """Speculative: an empty block carries no threshold, so it should keep the legacy identity."""
    plain, empty = parse(_document(), "skill"), parse(_document(statistics={}), "skill")
    assert empty.identity == plain.identity


def test_parse_rejects_a_null_block() -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics=None), "skill")
    assert caught.value.code == "contract-statistics-invalid"


def test_parse_negative_zero_is_not_a_distinct_threshold() -> None:
    """Speculative low: -0.0 and 0.0 are the same threshold but hash differently."""
    zero, negative = (parse(_document(statistics={"min_gain": value}), "skill") for value in (0.0, -0.0))
    assert zero.identity == negative.identity


@pytest.mark.parametrize("block", [{"min_gain": {"v": 1}}, {"min_gain": "nan"}, {"min_gain": "inf"},
                                   {"min_lower_bound": False}])
def test_parse_rejects_hostile_value_types(block: dict[str, object]) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics=block), "skill")
    assert caught.value.code == "contract-statistics-invalid"


def test_parse_floats_and_ints_of_equal_value_share_one_identity() -> None:
    one, other = (parse(_document(statistics={"min_gain": value}), "skill") for value in (1, 1.0))
    assert one.identity == other.identity


# --- gate ------------------------------------------------------------------------------------------------

def test_float_edge_min_gain_just_above_the_delta_blocks_and_equal_passes() -> None:
    same = [0.1, 0.1, 0.1]
    delta = verdict(same).delta
    assert verdict(same, thresholds=Statistics(min_gain=delta)).verdict == "promote"
    above = verdict(same, thresholds=Statistics(min_gain=math.nextafter(delta, 1.0)))
    assert (above.verdict, above.reasons) == ("inconclusive", ("min-gain",))


def test_float_edge_lower_bound_just_below_the_margin_passes_and_just_above_blocks() -> None:
    same = [0.3, 0.3, 0.3]
    delta = verdict(same).delta
    assert verdict(same, thresholds=Statistics(min_lower_bound=math.nextafter(delta, 0.0))).verdict == "promote"
    assert verdict(same, thresholds=Statistics(min_lower_bound=math.nextafter(delta, 1.0))).reasons == ("lower-bound",)


def test_floors_never_rescue_a_reject_or_an_inconclusive_and_never_add_reasons() -> None:
    for values, expected in (([-1.0, -2.0], "reject"), ([-1.0, 1.0, 0.0], "inconclusive")):
        result = verdict(values, thresholds=Statistics(min_gain=100.0, min_lower_bound=100.0))
        assert (result.verdict, result.reasons) == (expected, ())


def test_zero_floors_keep_a_zero_se_negative_delta_a_reject() -> None:
    assert verdict([-1.0, -1.0], thresholds=Statistics(0.0, 0.0)).verdict == "reject"


def test_a_failed_floor_keeps_the_real_delta_and_se() -> None:
    plain = verdict(SPREAD)
    floored = verdict(SPREAD, thresholds=Statistics(min_gain=50.0))
    assert (floored.delta, floored.se, floored.cases) == (plain.delta, plain.se, plain.cases)


def test_a_promote_that_misses_only_one_floor_names_only_that_floor() -> None:
    assert verdict(SPREAD, thresholds=Statistics(min_gain=3.0)).reasons == ("min-gain",)
    assert verdict(SPREAD, thresholds=Statistics(min_lower_bound=3.0)).reasons == ("lower-bound",)


def test_a_nan_min_gain_does_not_silently_disable_the_floor() -> None:
    """FINDING (tamper surface): NaN min_gain fails open, while NaN min_lower_bound fails closed."""
    assert verdict(SPREAD, thresholds=Statistics(min_gain=math.nan)).verdict != "promote"


# --- resolution ------------------------------------------------------------------------------------------

def test_a_zero_flag_overrides_a_positive_contract_value_even_though_zero_is_falsy() -> None:
    contract = parse(_document(statistics={"min_gain": 0.4, "min_lower_bound": 0.2}), "skill")
    assert resolve_statistics(contract, {"min_gain": 0.0, "min_lower_bound": 0.0}) == Statistics()


def test_an_empty_contract_block_resolves_to_defaults() -> None:
    assert resolve_statistics(parse(_document(statistics={}), "skill"), {}) == Statistics()


# --- workflow: freeze, tamper, resume --------------------------------------------------------------------

def _prepare_only(target: Path, out: Path, write_draft: Callable[..., list[str]],
                  approvals: Callable[..., tuple[str, int]], *, statistics: dict[str, float] | None = None) -> None:
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics=statistics)
    assert prepared.value.code == "live-required"


def _tamper(out: Path, value: object) -> None:
    path = out / "run.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    if value is ...:
        del document["statistics"]
    else:
        document["statistics"] = value
    _ = path.write_text(json.dumps(document))


def test_a_frozen_floor_beats_a_later_contract_edit(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals, statistics={"min_gain": 0.5})
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"statistics": {"min_gain": 9.0}}))
    result = run(target, out, MODEL, live=True, factory=_Provider)
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["reasons"]) == ("promote", [])
    assert read(out / "run.json")["statistics"] == Statistics(min_gain=0.5).data()


def test_a_tampered_frozen_floor_is_the_one_the_gate_uses(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals)
    _tamper(out, Statistics(min_gain=5.0).data())
    result = run(target, out, MODEL, live=True, factory=_Provider)
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["reasons"]) == ("inconclusive", ["min-gain"])


@pytest.mark.parametrize("shape", [
    ..., {}, {"min_gain": 0.0}, {"min_gain": "x", "min_lower_bound": 0.0}, {"min_gain": -1.0, "min_lower_bound": 0.0},
    {"min_gain": True, "min_lower_bound": 0.0}, {"min_gain": 10**400, "min_lower_bound": 0.0},
    {"min_gain": 0.0, "min_lower_bound": 0.0, "extra": 1.0}, []])
def test_a_malformed_frozen_statistics_map_stops_as_tampered(
        shape: object, tmp_path: Path, make_target: Callable[..., Path],
        write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals)
    _tamper(out, shape)
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, live=True, factory=_Provider)
    assert caught.value.code == "run-record-tampered"


def test_a_resume_flag_equal_to_the_contract_resolved_value_is_accepted_and_others_differ(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"statistics": {"min_gain": 0.4}}))
    _prepare_only(target, out, write_draft, approvals)
    with pytest.raises(Stop) as same:
        _ = run(target, out, MODEL, statistics={"min_gain": 0.4})
    assert same.value.code == "live-required"
    with pytest.raises(CodedError) as zero:
        _ = run(target, out, MODEL, statistics={"min_gain": 0.0})
    assert zero.value.code == "run-config-differs"
    with pytest.raises(CodedError) as other:
        _ = run(target, out, MODEL, statistics={"min_lower_bound": 1e-9})
    assert other.value.code == "run-config-differs"


def test_a_nan_flag_is_refused_by_name(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    with pytest.raises(ValueError, match="--min-lower-bound"):
        _ = run(make_target(tmp_path), tmp_path / "run", MODEL, statistics={"min_lower_bound": math.nan})


def test_a_fresh_invalid_flag_creates_no_run_record(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    out = tmp_path / "run"
    with pytest.raises(ValueError):
        _ = run(make_target(tmp_path), out, MODEL, statistics={"min_gain": -1.0})
    assert not (out / "run.json").exists()


def test_a_negative_zero_flag_is_accepted_and_equals_zero_on_resume(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals, statistics={"min_gain": -0.0})
    with pytest.raises(Stop):
        _ = run(target, out, MODEL, statistics={"min_gain": 0.0})


def test_zero_flags_give_a_default_gate_with_empty_reasons(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=_Provider,
                 statistics={"min_gain": 0.0, "min_lower_bound": 0.0})
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["reasons"]) == ("promote", [])


# --- CLI -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["-0.1", "nan", "-inf", "inf", "1e999"])
def test_cli_refuses_a_bad_statistics_flag_by_name(
        text: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "skill"
    base = ["run", "--target", str(target), "--out", str(tmp_path / "run"), "--model", MODEL]
    code = main([*base, f"--min-gain={text}"])
    captured = capsys.readouterr()
    assert code != 0
    assert "--min-gain must be a finite number of at least 0" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


@pytest.mark.parametrize("text", ["abc", ""])
def test_cli_refuses_an_unparsable_statistics_flag_with_a_nonzero_exit(
        text: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["run", "--target", str(tmp_path / "skill"), "--out", str(tmp_path / "run"), "--model", MODEL]
    code = main([*base, f"--min-gain={text}"])
    assert code != 0
    assert "Traceback" not in "".join(capsys.readouterr())


def test_cli_accepts_zero_and_exponent_forms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(*_arguments: object, **options: object) -> dict[str, object]:
        seen.update(options)
        return {}

    monkeypatch.setattr("skillz_experiments._cli.run_workflow", fake)
    base = ["run", "--target", str(tmp_path), "--out", str(tmp_path / "run"), "--model", MODEL]
    assert main([*base, "--min-gain", "0", "--min-lower-bound", "1e-3"]) == 0
    assert seen["statistics"] == {"min_gain": 0.0, "min_lower_bound": 0.001}
