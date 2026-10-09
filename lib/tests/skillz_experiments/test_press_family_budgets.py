"""Press pass on curd C2 (skillz-gate-statistics): adversarial tests for family regression budgets."""
from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import parse
from skillz_experiments._gate import Statistics, verdict
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import Stop, resolve_statistics, run

MODEL = "local-test"

pytestmark = pytest.mark.usefixtures("host_login")

CASES = {"a1": -1.0, "a2": -1.0, "b1": 2.0, "b2": 2.0, "b3": 1.0, "b4": 1.0, "c1": 3.0, "c2": 3.0}
FAMILIES = {"a1": "a", "a2": "a", "b1": "b", "b2": "b", "b3": "b", "b4": "b", "c1": "c", "c2": "c"}
NOISY = [-3.0, -2.0, -4.0]


def _document(**extra: object) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": {"echo": {"grader": "exact-json"}}, "editable": [], **extra}


def _padded(family: str, values: list[float]) -> tuple[dict[str, float], dict[str, str]]:
    """Return deltas and families: `values` in `family`, plus a strongly positive family `p` so the score rule promotes."""
    deltas = {f"{family}{index}": value for index, value in enumerate(values)}
    deltas |= {f"p{index}": 5.0 + index for index in range(6)}
    return deltas, {key: (family if key.startswith(family) and not key.startswith("p") else "p") for key in deltas}


def _upper(values: list[float]) -> float:
    import statistics
    return statistics.fmean(values) + 2 * (statistics.stdev(values) / math.sqrt(len(values)))


# --- the bound: strict less-than --------------------------------------------------------------------------

def test_verdict_upperBoundExactlyAtNegativeBudget_doesNotBreach() -> None:
    upper = _upper(NOISY)
    assert upper < 0
    deltas, families = _padded("x", NOISY)
    exact = verdict(deltas, thresholds=Statistics(family_budget=-upper), families=families)
    assert exact.reasons == () and exact.verdict == "promote"
    below = verdict(deltas, thresholds=Statistics(family_budget=math.nextafter(-upper, 0.0)), families=families)
    assert below.reasons == ("family-regression:x",)
    above = verdict(deltas, thresholds=Statistics(family_budget=math.nextafter(-upper, 10.0)), families=families)
    assert above.reasons == ()


def test_verdict_zeroSeMeanExactlyAtNegativeBudget_doesNotBreach() -> None:
    deltas, families = _padded("x", [-1.0, -1.0])
    assert verdict(deltas, thresholds=Statistics(family_budget=1.0), families=families).reasons == ()
    bite = verdict(deltas, thresholds=Statistics(family_budget=math.nextafter(1.0, 0.0)), families=families)
    assert bite.reasons == ("family-regression:x",)


def test_verdict_repeatedInexactDecimalsAtBudget_doNotBreachOnFloatNoise() -> None:
    """A family at the budget does not breach: fmean([-0.1] * 3) is -0.10000000000000002 and must not count as one."""
    deltas, families = _padded("x", [-0.1, -0.1, -0.1])
    assert verdict(deltas, thresholds=Statistics(family_budget=0.1), families=families).reasons == ()


def test_verdict_zeroBudgetAndMeanZeroFamily_doesNotBreach() -> None:
    deltas, families = _padded("x", [0.0, 0.0])
    assert verdict(deltas, thresholds=Statistics(family_budget=0.0), families=families).reasons == ()
    deltas, families = _padded("x", [-1e-12, -1e-12])
    assert verdict(deltas, thresholds=Statistics(family_budget=0.0), families=families).reasons == (
        "family-regression:x",)


# --- overrides ---------------------------------------------------------------------------------------------

def test_verdict_overrideForFamilyAbsentFromHoldout_isIgnoredAndNotSkipped() -> None:
    result = verdict(CASES, thresholds=Statistics(family_budgets={"ghost": 0.0, "train-only": 0.0}),
                     families=FAMILIES)
    assert (result.verdict, result.reasons, result.skipped_families) == ("promote", (), ())


def test_verdict_familiesMapNamingCasesOutsideTheDeltas_neverCreatesFamilies() -> None:
    families = FAMILIES | {"v1": "validation", "v2": "validation", "t1": "train"}
    result = verdict(CASES, thresholds=Statistics(family_budget=0.0), families=families)
    assert result.skipped_families == ()
    assert result.reasons == ("family-regression:a",)


def test_verdict_zeroOverrideWithNoGlobalBudget_isActiveNotOff() -> None:
    result = verdict(CASES, thresholds=Statistics(family_budget=None, family_budgets={"a": 0.0}), families=FAMILIES)
    assert result.reasons == ("family-regression:a",)


def test_verdict_overrideOnlyAffectsItsFamily_whenGlobalBudgetIsNone() -> None:
    other = {"a1": -1.0, "a2": -1.0, "c1": -1.0, "c2": -1.0, "b1": 9.0, "b2": 9.0, "b3": 9.0, "b4": 9.0}
    families = {key: key[0] for key in other}
    result = verdict(other, thresholds=Statistics(family_budgets={"a": 0.0}), families=families)
    assert result.reasons == ("family-regression:a",)


def test_verdict_lonelyFamilyWithOverride_isSkippedNotRejected() -> None:
    deltas = {"s1": -9.0} | {f"p{i}": 5.0 + i for i in range(6)}
    families = {key: "p" for key in deltas} | {"s1": "lone"}
    result = verdict(deltas, thresholds=Statistics(family_budgets={"lone": 0.0}), families=families)
    assert result.skipped_families == ("lone",)
    assert result.reasons == ()


# --- names ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["café", "\U0001f9c0 brie", "a:b:c", "line\nbreak", "x" * 10_000, " ", "‮RTL",
                                  "family-regression:a"])
def test_verdict_unusualFamilyNames_appearVerbatimInReasons(name: str) -> None:
    deltas, families = _padded("x", [-1.0, -1.0])
    families = {key: (name if value == "x" else value) for key, value in families.items()}
    result = verdict(deltas, thresholds=Statistics(family_budget=0.0), families=families)
    assert result.reasons == (f"family-regression:{name}",)
    assert result.verdict == "reject"


def test_verdict_emptyFamilyName_isStillGrouped() -> None:
    deltas, families = _padded("x", [-1.0, -1.0])
    families = {key: ("" if value == "x" else value) for key, value in families.items()}
    result = verdict(deltas, thresholds=Statistics(family_budget=0.0), families=families)
    assert result.reasons == ("family-regression:",)


def test_verdict_manyFamilies_reasonsAndSkippedAreSortedByName() -> None:
    names = [f"f{index:03d}" for index in range(40)]
    deltas: dict[str, float] = {}
    families: dict[str, str] = {}
    for position, name in enumerate(reversed(names)):
        for copy in range(2):
            key = f"{name}-{copy}" if position % 2 == 0 else f"{name}-0"
            deltas[key] = -1.0
            families[key] = name
    deltas |= {f"p{i}": 9.0 + i for i in range(30)}
    families |= {f"p{i}": "zz" for i in range(30)}
    result = verdict(deltas, thresholds=Statistics(family_budget=0.0), families=families)
    breached = [reason.split(":", 1)[1] for reason in result.reasons]
    assert breached == sorted(breached) and len(breached) == 20
    assert list(result.skipped_families) == sorted(result.skipped_families) and len(result.skipped_families) == 20
    assert set(breached).isdisjoint(result.skipped_families)


# --- combination ------------------------------------------------------------------------------------------

def test_verdict_familyBreachFloorsAndScoreReject_listsEachReasonAndRejects() -> None:
    floors = Statistics(min_gain=99.0, min_lower_bound=99.0, family_budget=0.0)
    promoted = verdict(CASES, thresholds=floors, families=FAMILIES)
    assert promoted.verdict == "reject"
    assert promoted.reasons == ("min-gain", "lower-bound", "family-regression:a")
    losing = {key: -abs(value) - 1.0 for key, value in CASES.items()} | {"z1": -2.0, "z2": -3.0}
    families = FAMILIES | {"z1": "z", "z2": "z"}
    plain = verdict(losing, thresholds=Statistics(family_budget=0.0), families=families)
    assert plain.verdict == "reject"
    assert plain.reasons and all(reason.startswith("family-regression:") for reason in plain.reasons)
    assert plain.reasons == tuple(sorted(plain.reasons))


def test_verdict_breachOnScoreRejectWithoutBudget_addsNothing() -> None:
    losing = {key: -1.0 - index for index, key in enumerate(CASES)}
    result = verdict(losing, families=FAMILIES)
    assert result.verdict == "reject" and result.reasons == ()


# --- non-finite and degenerate --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_verdict_nonFiniteDelta_skipsTheFamilyRuleButListsSkippedFamilies(bad: float) -> None:
    result = verdict(CASES | {"a1": bad, "x": 1.0}, thresholds=Statistics(family_budget=0.0),
                     families=FAMILIES | {"x": "solo"})
    assert result.verdict == "inconclusive"
    assert result.reasons == () and result.skipped_families == ("solo",)


def test_verdict_sequenceDeltasWithFamilies_raises() -> None:
    with pytest.raises(ValueError, match="keyed by case id"):
        _ = verdict([-1.0, -1.0, 2.0, 2.0, 3.0, 3.0], thresholds=Statistics(family_budget=0.0), families=FAMILIES)

def test_verdict_emptyFamiliesMapAndEmptyDeltas_areQuiet() -> None:
    assert verdict({}, thresholds=Statistics(family_budget=0.0), families={}).skipped_families == ()
    assert verdict(CASES, thresholds=Statistics(family_budget=0.0), families={}).verdict == "promote"


def test_verdict_oneCaseHoldout_listsSkippedFamilyWithoutBudget() -> None:
    result = verdict({"k": 1.0}, families={"k": "solo"})
    assert result.skipped_families == ("solo",)


def test_verdict_twoCaseHoldoutOneFamily_checksTheBudget() -> None:
    result = verdict({"k1": -1.0, "k2": -1.0}, thresholds=Statistics(family_budget=0.0),
                     families={"k1": "f", "k2": "f"})
    assert result.verdict == "reject" and result.reasons == ("family-regression:f",)


def test_verdict_nullBudgetEqualsAbsentBudget() -> None:
    null = verdict(CASES, thresholds=Statistics(family_budget=None), families=FAMILIES)
    assert null == verdict(CASES, families=FAMILIES)


# --- Statistics record ---------------------------------------------------------------------------------

def test_statistics_familyBudgetsMutation_isRefusedAndCopiedAtConstruction() -> None:
    source = {"a": 1.0}
    stats = Statistics(family_budgets=source)
    source["a"] = 0.0
    source["b"] = 0.0
    assert dict(stats.family_budgets) == {"a": 1.0}
    with pytest.raises(TypeError):
        cast(dict[str, float], stats.family_budgets)["a"] = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(stats, "family_budget", 0.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(stats, "family_budgets", {})


def test_statistics_dataOutput_isIndependentOfTheLiveMap() -> None:
    stats = Statistics(family_budgets={"a": 1.0})
    cast(dict[str, float], stats.data()["family_budgets"])["a"] = 0.0
    assert dict(stats.family_budgets) == {"a": 1.0}


def test_statistics_equalityAndHash_ignoreMapInsertionOrder() -> None:
    assert Statistics(family_budgets={"a": 1.0, "b": 2.0}) == Statistics(family_budgets={"b": 2.0, "a": 1.0})


@pytest.mark.parametrize("shape", [
    {"family_budgets": {"a": 10**400}}, {"family_budgets": {"a": math.nan}}, {"family_budgets": {"a": -0.5}},
    {"family_budgets": {"a": True}}, {"family_budgets": {"a": False}}, {"family_budgets": {"a": [1]}},
    {"family_budgets": {"a": {"b": 1}}}, {"family_budgets": {1: 1.0}}, {"family_budgets": {None: 1.0}},
    {"family_budget": True}, {"family_budget": 10**400}, {"family_budget": [0]}])
def test_statistics_declaredHostileValues_areRefused(shape: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _ = Statistics.declared(shape)


@pytest.mark.parametrize("block", [{"family_budgets": {"a": 10**400}}, {"family_budgets": {"a": False}},
                                   {"family_budgets": {"a": 0.0, "b": math.nan}}])
def test_contract_hostileMapValues_areRefusedByCode(block: dict[str, object]) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics=block), "skill")
    assert caught.value.code == "contract-statistics-invalid"


def test_contract_negativeZeroBudgets_hashLikeZero() -> None:
    neg = parse(_document(statistics={"family_budget": -0.0, "family_budgets": {"a": -0.0}}), "skill")
    pos = parse(_document(statistics={"family_budget": 0.0, "family_budgets": {"a": 0.0}}), "skill")
    assert neg.identity == pos.identity


def test_contract_statisticsWithoutNewKeys_keepsPreC2Data() -> None:
    contract = parse(_document(statistics={"min_gain": 0.5, "min_lower_bound": 0.25}), "skill")
    assert contract.data()["statistics"] == {"min_gain": 0.5, "min_lower_bound": 0.25}


def test_contract_nullFamilyBudget_flagOverridesAndNoFlagStaysNone() -> None:
    contract = parse(_document(statistics={"family_budget": None}), "skill")
    assert resolve_statistics(contract, {}).family_budget is None
    assert resolve_statistics(contract, {"family_budget": 0.0}).family_budget == 0.0
    set_one = parse(_document(statistics={"family_budget": 0.5}), "skill")
    assert resolve_statistics(set_one, {}).family_budget == 0.5


def test_contract_flagBudgetOverridesContractBudgetButNotMapEntry() -> None:
    contract = parse(_document(statistics={"family_budgets": {"a": 0.5}}), "skill")
    resolved = resolve_statistics(contract, {"family_budget": 9.0})
    result = verdict(CASES, thresholds=resolved, families=FAMILIES)
    assert result.reasons == ("family-regression:a",)


# --- workflow --------------------------------------------------------------------------------------------

REGRESSING = ("f0", "f1")


@final
class _Provider:
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


def _prepare_only(target: Path, out: Path, write_draft: Callable[..., list[str]],
                  approvals: Callable[..., tuple[str, int]], statistics: dict[str, float] | None) -> None:
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics=statistics)
    assert prepared.value.code == "live-required"


def _gate(result: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], result["gate"])


def _tamper(out: Path, edit: Callable[[dict[str, object]], object]) -> None:
    """Rewrite the frozen statistics block of a run record. The record is unsigned."""
    path = out / "run.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = edit(cast(dict[str, object], document["statistics"]))
    _ = path.write_text(json.dumps(document))


def test_run_resumeWithFlagOmitted_keepsTheFrozenBudget(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals, {"family_budget": 0.0})
    result = run(target, out, MODEL, live=True, factory=_Provider)
    assert _gate(result)["verdict"] == "reject"


def test_run_frozenBudgetDroppedFromRecord_isNotSilentlyDisabled(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """A record that loses `family_budget` after freezing 0.0 must stop as tampered or keep the rule."""
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals, {"family_budget": 0.0})
    _tamper(out, lambda stats: stats.pop("family_budget"))
    try:
        result = run(target, out, MODEL, live=True, factory=_Provider)
    except Stop as stop:
        assert stop.code == "run-record-tampered"
        return
    assert _gate(result)["verdict"] == "reject"


# A well-formed value edit in run.json is out of scope: the record checks shape, not binding (C1 decision L1).

def test_run_frozenMapDroppedFromRecord_isNotSilentlyDisabled(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    path = target / "evals/autoimprove.json"
    document = cast(dict[str, object], json.loads(path.read_text()))
    _ = path.write_text(json.dumps(document | {"statistics": {"family_budgets": {"f0": 0.0, "f1": 0.0}}}))
    _prepare_only(target, out, write_draft, approvals, None)
    _tamper(out, lambda stats: stats.pop("family_budgets"))
    try:
        result = run(target, out, MODEL, live=True, factory=_Provider)
    except Stop as stop:
        assert stop.code == "run-record-tampered"
        return
    assert _gate(result)["verdict"] == "reject"


def test_run_resumeFlagAgainstOldShapeRecord_isRefusedAsTampered(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _prepare_only(target, out, write_draft, approvals, None)
    _tamper(out, lambda stats: (stats.pop("family_budget"), stats.pop("family_budgets")))
    with pytest.raises(Stop) as caught:
        _ = run(target, out, MODEL, statistics={"family_budget": 0.0})
    assert caught.value.code == "run-record-tampered"


def test_run_zeroFlagBudget_isNotTreatedAsOff(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    out = tmp_path / "run"
    _ = write_draft(out)
    target = make_target(tmp_path)
    case_hash, calls = approvals(target, out)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=_Provider,
                 statistics={"family_budget": 0.0})
    assert _gate(result)["verdict"] == "reject"


@pytest.mark.parametrize("bad", [math.nan, -math.inf, math.inf, -0.001, True, False, 10**400])
def test_run_badFamilyBudgetFlag_isRefusedBeforeAnyRecord(
        bad: float, tmp_path: Path, make_target: Callable[..., Path]) -> None:
    out = tmp_path / "run"
    with pytest.raises(ValueError, match="--family-budget"):
        _ = run(make_target(tmp_path), out, MODEL, statistics={"family_budget": bad})
    assert not (out / "run.json").exists()


def test_run_familyBudgetsAsFlag_isRefusedAsUnknown(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    with pytest.raises(ValueError, match="unknown statistics flag"):
        _ = run(make_target(tmp_path), tmp_path / "run", MODEL, statistics={"family_budgets": 0.0})
