"""Curd C2 (skillz-gate-statistics): per-family regression budgets, the override map, and skipped families."""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast, final

import pytest
from typing_extensions import override

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._cli import main
from skillz_experiments._contract import parse
from skillz_experiments._gate import DEFAULT_STATISTICS, Statistics, simulate, verdict
from skillz_experiments._records import read
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import Stop, export, resolve_statistics, run

MODEL = "local-test"


def _open_unit(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(cast(float, value))
    return number if 0 < number < 1 else None

pytestmark = pytest.mark.usefixtures("host_login")


def _document(**extra: object) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": {"echo": {"grader": "exact-json"}}, "editable": [], **extra}


# Family a: two cases, mean -1, SE 0. Family b: four cases, mean 1.5, SE > 0. The score rule promotes.
CASES = {"a1": -1.0, "a2": -1.0, "b1": 2.0, "b2": 2.0, "b3": 1.0, "b4": 1.0, "c1": 3.0, "c2": 3.0}
FAMILIES = {"a1": "a", "a2": "a", "b1": "b", "b2": "b", "b3": "b", "b4": "b", "c1": "c", "c2": "c"}


# --- the verdict rule ------------------------------------------------------------------------------------

def test_no_budget_keeps_the_verdict_and_lists_a_lone_family_as_skipped() -> None:
    plain = verdict(CASES)
    shown = verdict(CASES, families=FAMILIES | {"c2": "d", "c1": "d"} | {"b4": "lone"})
    assert (shown.verdict, shown.reasons, shown.delta, shown.se) == (plain.verdict, (), plain.delta, plain.se)
    assert shown.skipped_families == ("lone",)
    assert plain.skipped_families == ()


def test_a_zero_se_family_rejects_when_its_mean_is_below_the_negative_budget() -> None:
    result = verdict(CASES, thresholds=Statistics(family_budget=0.5), families=FAMILIES)
    assert verdict(CASES).verdict == "promote"
    assert (result.verdict, result.reasons) == ("reject", ("family-regression:a",))


def test_a_zero_se_family_within_its_budget_does_not_reject() -> None:
    assert verdict(CASES, thresholds=Statistics(family_budget=1.0), families=FAMILIES).verdict == "promote"
    assert verdict(CASES, thresholds=Statistics(family_budget=2.0), families=FAMILIES).reasons == ()


def test_a_noisy_family_needs_its_upper_bound_below_the_budget() -> None:
    values = {"x1": -3.0, "x2": -2.0, "x3": -4.0, "y1": 1.0, "y2": 1.0, "y3": 1.0, "y4": 3.0, "y5": 3.0}
    families = {key: key[0] for key in values}
    upper = -3.0 + 2 * (1.0 / math.sqrt(3))
    assert upper < 0
    inside = Statistics(family_budget=-upper + 1e-6)
    assert verdict(values, thresholds=inside, families=families).reasons == ()
    breach = Statistics(family_budget=-upper - 1e-6)
    assert verdict(values, thresholds=breach, families=families).reasons == ("family-regression:x",)
    assert verdict(values, thresholds=breach, families=families).verdict == "reject"


def test_a_breach_rejects_from_inconclusive_and_appends_after_floor_reasons() -> None:
    spread = {"a1": -1.0, "a2": -1.0, "b1": 1.0, "b2": 1.0, "b3": 1.0, "b4": 1.0, "b5": 1.0, "b6": 1.0}
    families = {key: key[0] for key in spread}
    assert verdict(spread).verdict == "inconclusive"
    assert verdict(spread, thresholds=Statistics(family_budget=0.0), families=families).verdict == "reject"
    floors = Statistics(min_gain=9.0, family_budget=0.0)
    promoted = verdict(CASES, thresholds=floors, families=FAMILIES)
    assert promoted.verdict == "reject"
    assert promoted.reasons == ("min-gain", "family-regression:a")


def test_breaches_list_in_family_name_order() -> None:
    values = {"z1": -1.0, "z2": -1.0, "m1": -1.0, "m2": -1.0, "k1": 5.0, "k2": 5.0, "k3": 5.0, "k4": 5.0}
    families = {key: key[0] for key in values}
    result = verdict(values, thresholds=Statistics(family_budget=0.0), families=families)
    assert result.reasons == ("family-regression:m", "family-regression:z")


def test_families_with_fewer_than_two_cases_are_skipped_in_name_order_and_never_reject() -> None:
    values = {"s1": -5.0, "t1": -5.0, "u1": 1.0, "u2": 1.0, "u3": 1.0, "u4": 1.0, "u5": 1.0}
    families = {"s1": "zeta", "t1": "alpha", "u1": "u", "u2": "u", "u3": "u", "u4": "u", "u5": "u"}
    result = verdict(values, thresholds=Statistics(family_budget=0.0), families=families)
    assert result.skipped_families == ("alpha", "zeta")
    assert result.reasons == ()
    assert result.verdict == verdict(values).verdict


def test_a_holdout_of_one_case_skips_its_family() -> None:
    result = verdict({"only": -3.0}, thresholds=Statistics(family_budget=0.0), families={"only": "f"})
    assert (result.verdict, result.skipped_families) == ("inconclusive", ("f",))


def test_a_case_without_a_family_is_ignored() -> None:
    result = verdict(CASES, thresholds=Statistics(family_budget=0.0), families={"a1": "a"})
    assert result.skipped_families == ("a",)
    assert result.reasons == ()


def test_a_per_family_budget_overrides_the_global_budget_in_both_directions() -> None:
    strict = Statistics(family_budget=5.0, family_budgets={"a": 0.5})
    assert verdict(CASES, thresholds=strict, families=FAMILIES).reasons == ("family-regression:a",)
    lenient = Statistics(family_budget=0.0, family_budgets={"a": 5.0})
    assert verdict(CASES, thresholds=lenient, families=FAMILIES).verdict == "promote"
    only = Statistics(family_budgets={"a": 0.5})
    assert verdict(CASES, thresholds=only, families=FAMILIES).reasons == ("family-regression:a",)
    other = Statistics(family_budgets={"zzz": 0.0})
    assert verdict(CASES, thresholds=other, families=FAMILIES).verdict == "promote"


def test_a_zero_budget_override_is_not_mistaken_for_no_budget() -> None:
    zero = Statistics(family_budget=5.0, family_budgets={"a": 0.0})
    assert verdict(CASES, thresholds=zero, families=FAMILIES).reasons == ("family-regression:a",)


def test_defaults_keep_every_verdict_and_the_pinned_simulation() -> None:
    for values in ([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0], [0.0, 0.0], [1.0], [], [1.0, -1.0, 0.1]):
        assert verdict(values, thresholds=DEFAULT_STATISTICS).reasons == ()
        assert verdict(values).skipped_families == ()
    assert simulate() == simulate(trials=2000, cases=10)
    result = simulate()
    assert (result.trials, result.cases, result.rate_2se, result.rate_one_holdout) == (2000, 10, 0.0365, 0.8115)


# --- the Statistics record -------------------------------------------------------------------------------

def test_the_frozen_map_holds_both_keys_and_round_trips() -> None:
    base = Statistics()
    assert base.data() == {"min_gain": 0.0, "min_lower_bound": 0.0, "family_budget": None, "family_budgets": {},
                           "max_token_increase_per_gain": None, "min_token_saving": None}
    full = Statistics(family_budget=0.25, family_budgets={"b": 1.0, "a": 2.0})
    assert Statistics.parse(cast(object, json.loads(json.dumps(full.data())))) == full
    assert list(cast(dict[str, float], full.data()["family_budgets"])) == ["a", "b"]
    assert isinstance(full.family_budgets, MappingProxyType)


def test_a_record_without_the_family_keys_is_refused() -> None:
    with pytest.raises(ValueError, match="lacks fields: family_budget, family_budgets"):
        _ = Statistics.parse({"min_gain": 0.5, "min_lower_bound": 0.0})


def test_the_flag_registry_lists_the_scalar_flag_but_not_the_map() -> None:
    assert Statistics.flags() == ("min_gain", "min_lower_bound", "family_budget", "max_token_increase_per_gain",
                                  "min_token_saving")
    assert "family_budgets" in Statistics.names()


@pytest.mark.parametrize("shape", [
    {"min_gain": 0.0, "min_lower_bound": 0.0, "family_budget": -1.0},
    {"min_gain": 0.0, "min_lower_bound": 0.0, "family_budget": "x"},
    {"min_gain": 0.0, "min_lower_bound": 0.0, "family_budgets": []},
    {"min_gain": 0.0, "min_lower_bound": 0.0, "family_budgets": {"a": -1.0}},
    {"min_gain": None, "min_lower_bound": 0.0}])
def test_a_malformed_record_map_is_refused(shape: object) -> None:
    with pytest.raises(ValueError):
        _ = Statistics.parse(shape)


# --- contract --------------------------------------------------------------------------------------------

def test_a_contract_accepts_family_budget_and_the_map() -> None:
    contract = parse(_document(statistics={"family_budget": 0.1, "family_budgets": {"a": 0.0, "b": 1}}), "skill")
    resolved = resolve_statistics(contract, {})
    assert (resolved.family_budget, dict(resolved.family_budgets)) == (0.1, {"a": 0.0, "b": 1.0})


def test_a_flag_beats_the_contract_budget_but_keeps_the_map() -> None:
    contract = parse(_document(statistics={"family_budget": 0.1, "family_budgets": {"a": 0.3}}), "skill")
    resolved = resolve_statistics(contract, {"family_budget": 0.0})
    assert (resolved.family_budget, dict(resolved.family_budgets)) == (0.0, {"a": 0.3})


@pytest.mark.parametrize("block", [
    {"family_budget": -0.1}, {"family_budget": True}, {"family_budget": "1"}, {"family_budget": math.inf},
    {"family_budget": math.nan}, {"family_budget": 10**400},
    {"family_budgets": ["a"]}, {"family_budgets": "a"}, {"family_budgets": None}, {"family_budgets": {"": 1.0}},
    {"family_budgets": {"a": -1.0}}, {"family_budgets": {"a": True}}, {"family_budgets": {"a": math.inf}},
    {"family_budgets": {"a": "1"}}, {"family_budgets": {"a": None}}])
def test_an_invalid_family_value_in_a_contract_is_refused_by_code(block: dict[str, object]) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics=block), "skill")
    assert caught.value.code == "contract-statistics-invalid"


def test_a_contract_without_the_new_keys_hashes_as_before() -> None:
    plain = parse(_document(), "skill")
    floors = parse(_document(statistics={"min_gain": 0.5}), "skill")
    assert "statistics" not in plain.data()
    assert floors.data()["statistics"] == {"min_gain": 0.5}
    with_map = parse(_document(statistics={"min_gain": 0.5, "family_budgets": {"a": 0.1}}), "skill")
    assert with_map.identity != floors.identity
    same = parse(_document(statistics={"family_budgets": {"b": 1.0, "a": 0.1}}), "skill")
    swapped = parse(_document(statistics={"family_budgets": {"a": 0.1, "b": 1.0}}), "skill")
    assert same.identity == swapped.identity


# --- workflow: flag, freeze, resume, gate record, summary, export ----------------------------------------

REGRESSING = ("f0", "f1")


class _Provider:
    """A fake provider. `improved` always wins the search. On the holdout, families f0 and f1 reverse that."""

    def __init__(self, _model: str, _budget: Budget, _checkpoint: Callable[[], None]) -> None:
        pass

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        improved = "improved" in candidate.files["SKILL.md"]
        score = float(improved != (holdout and case.family in REGRESSING))
        return {"score": score, "candidate_hash": candidate.identity, "case_hash": case.identifier}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout
        assert schema is not None
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


@final
class _Tie(_Provider):
    """A provider whose reflection returns the seed files, so the winner equals the baseline."""

    @override
    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del candidate, case, holdout
        assert schema is not None
        seed = cast(dict[str, str], json.loads(prompt.rsplit("\n", 1)[1])["candidate"])
        return {"answer": {name: seed[name] for name in cast(list[str], schema["required"])}}


def test_a_tie_run_lists_every_one_case_family_as_skipped(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=12, per_family=1)
    case_hash, calls = approvals(target, out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=_Tie,
                 statistics={"family_budget": 0.0})
    gate = cast(dict[str, object], result["gate"])
    skipped = cast(list[str], gate["skipped_families"])
    assert gate["reason"] == "winner-equals-baseline"
    assert skipped and skipped == sorted(skipped) and len(skipped) == gate["cases"]

def _prepare_only(target: Path, out: Path, write_draft: Callable[..., list[str]],
                  approvals: Callable[..., tuple[str, int]], *, statistics: dict[str, float] | None = None) -> None:
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics=statistics)
    assert prepared.value.code == "live-required"


def _finish(target: Path, out: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
            statistics: dict[str, float] | None) -> dict[str, object]:
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    return run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=_Provider,
               statistics=statistics)


def test_a_default_run_writes_a_stable_gate_shape_and_the_frozen_map_holds_both_keys(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    out = tmp_path / "run"
    result = _finish(make_target(tmp_path), out, write_draft, approvals, None)
    gate = cast(dict[str, object], result["gate"])
    assert gate["skipped_families"] == []
    assert read(out / "run.json")["statistics"] == Statistics().data()
    assert "family-regression" not in " ".join(cast(list[str], gate["reasons"]))


def test_a_family_budget_flag_rejects_a_regressing_holdout_family_and_summary_and_export_show_the_gate(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    out = tmp_path / "run"
    result = _finish(make_target(tmp_path), out, write_draft, approvals, {"family_budget": 0.0})
    gate = cast(dict[str, object], result["gate"])
    reasons = cast(list[str], gate["reasons"])
    assert gate["verdict"] == "reject"
    assert reasons == ["family-regression:f0"]
    assert gate["skipped_families"] == []
    assert read(out / "run.json")["statistics"]["family_budget"] == 0.0  # pyright: ignore[reportIndexIssue]
    _ = export(out, tmp_path / "export")
    report = cast(dict[str, object], json.loads((tmp_path / "export" / "report.json").read_text()))
    assert report["gate"] == gate


def test_skipped_families_reach_summary_and_export(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out, families=12, per_family=1)
    case_hash, calls = approvals(target, out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=_Provider,
                 statistics={"family_budget": 0.0})
    gate = cast(dict[str, object], result["gate"])
    skipped = cast(list[str], gate["skipped_families"])
    assert skipped and skipped == sorted(skipped) and len(skipped) == gate["cases"]
    assert not any(reason.startswith("family-regression") for reason in cast(list[str], gate["reasons"]))
    _ = export(out, tmp_path / "export")
    report = cast(dict[str, object], json.loads((tmp_path / "export" / "report.json").read_text()))
    assert cast(dict[str, object], report["gate"])["skipped_families"] == skipped


def test_a_resume_flag_must_match_the_frozen_family_budget(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals, statistics={"family_budget": 0.5})
    with pytest.raises(Stop) as same:
        _ = run(target, out, MODEL, statistics={"family_budget": 0.5})
    assert same.value.code == "live-required"
    with pytest.raises(CodedError) as other:
        _ = run(target, out, MODEL, statistics={"family_budget": 0.0})
    assert other.value.code == "run-config-differs"
    assert "--family-budget" in str(other.value)


def test_a_frozen_budget_beats_a_later_contract_edit(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"statistics": {"family_budgets": {"f0": 0.0, "f1": 0.0}}}))
    _prepare_only(target, out, write_draft, approvals)
    _ = path.write_text(json.dumps(document | {"statistics": {"family_budgets": {"f0": 99.0, "f1": 99.0}}}))
    result = run(target, out, MODEL, live=True, factory=_Provider)
    assert cast(dict[str, object], result["gate"])["verdict"] == "reject"
    assert read(out / "run.json")["statistics"]["family_budgets"] == {"f0": 0.0, "f1": 0.0}  # pyright: ignore[reportIndexIssue]


@pytest.mark.parametrize("shape", [
    {"min_gain": 0.0, "min_lower_bound": 0.0, "family_budget": -1.0},
    {"min_gain": 0.0, "min_lower_bound": 0.0, "family_budgets": {"": 1.0}}])
def test_a_malformed_frozen_family_value_stops_as_tampered(
        shape: object, tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals)
    path = out / "run.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    document["statistics"] = shape
    _ = path.write_text(json.dumps(document))
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, live=True, factory=_Provider)
    assert caught.value.code == "run-record-tampered"


def test_a_bad_family_budget_flag_is_refused_by_name(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    target = make_target(tmp_path)
    for bad in (-1.0, math.nan, math.inf, True):
        with pytest.raises(ValueError, match="--family-budget"):
            _ = run(target, tmp_path / "run", MODEL, statistics={"family_budget": bad})


@final
@dataclass(frozen=True)
class _Ranged(Statistics):
    ratio: float | None = field(default=None, metadata={
        "flag": True, "nullable": True, "check": _open_unit, "rule": "a number above 0 and below 1"})


def test_a_field_check_in_metadata_is_the_only_edit_a_range_flag_needs() -> None:
    assert _Ranged.flags()[-1] == "ratio"
    assert _Ranged.declared({"ratio": 0.5}) == {"ratio": 0.5}
    assert _Ranged.declared({"ratio": None}) == {"ratio": None}
    for bad in (0.0, 1.0, -0.5, math.nan, True):
        with pytest.raises(ValueError, match="ratio must be a number above 0 and below 1"):
            _ = _Ranged.declared({"ratio": bad})
    assert _Ranged.parse(_Ranged().data() | {"ratio": 0.25}).data()["ratio"] == 0.25
    assert _Ranged(ratio=0.25).data()["ratio"] == _Ranged(ratio=0.25).ratio


def test_run_checks_each_flag_through_the_field_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skillz_experiments._workflow.Statistics", _Ranged)
    with pytest.raises(ValueError, match="--ratio must be a number above 0 and below 1"):
        _ = run(tmp_path, tmp_path / "run", MODEL, statistics={"ratio": 1.0})


def test_a_family_budgets_key_naming_no_draft_family_stops_with_a_code(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"statistics": {"family_budgets": {"f0": 0.0, "f-typo": 0.0}}}))
    _ = write_draft(out)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, MODEL, approve_cases="unused")
    assert caught.value.code == "contract-family-unknown"
    assert "f-typo" in str(caught.value) and "f0," in str(caught.value)


def test_a_blank_family_budgets_key_is_refused() -> None:
    with pytest.raises(ValueError, match="keys must be nonempty strings"):
        _ = Statistics.declared({"family_budgets": {" ": 1.0}})


def test_statistics_with_a_budget_map_is_hashable_and_equal_by_value() -> None:
    first, second = Statistics(family_budgets={"b": 1.0, "a": 2.0}), Statistics(family_budgets={"a": 2.0, "b": 1.0})
    assert first == second and hash(first) == hash(second)


def test_a_completed_run_still_summarises_when_its_statistics_are_unreadable(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    out = tmp_path / "run"
    target = make_target(tmp_path)
    done = _finish(target, out, write_draft, approvals, None)
    path = out / "run.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    del document["statistics"]
    _ = path.write_text(json.dumps(document))
    assert run(target, out, MODEL, live=True)["gate"] == done["gate"]


def test_the_cli_passes_family_budget_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(*_args: object, **kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr("skillz_experiments._cli.run_workflow", fake)
    base = ["run", "--target", str(tmp_path / "skill"), "--out", str(tmp_path / "run"), "--model", MODEL]
    assert main([*base, "--family-budget=0.25"]) == 0
    assert seen["statistics"] == {"family_budget": 0.25}
    assert main(base) == 0
    assert seen["statistics"] is None
