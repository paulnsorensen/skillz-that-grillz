"""Token-cost rule: verdicts, selection tie-break, reflection text, flags, and records (AC-10..AC-16)."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._cli import main
from skillz_experiments._contract import parse
from skillz_experiments._evaluator import task_tokens
from skillz_experiments._gate import DEFAULT_STATISTICS, Statistics, simulate, verdict
from skillz_experiments._records import read
from skillz_experiments._runtime import Budget
from skillz_experiments._workflow import (
    Stop,
    _reflection_request,  # pyright: ignore[reportPrivateUsage] -- unit test of the private prompt builder
    _Session,  # pyright: ignore[reportPrivateUsage] -- tests build a bare session
    export,
    resolve_statistics,
    run,
)

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"
SENTENCE = " Prefer fewer tokens when correctness is equal."


def _document(**extra: object) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": {"echo": {"grader": "exact-json"}}, "editable": [], **extra}


def _tokens(*pairs: tuple[float | None, float | None]) -> dict[str, tuple[float | None, float | None]]:
    return {f"c{index}": pair for index, pair in enumerate(pairs)}


def _deltas(*values: float) -> dict[str, float]:
    return {f"c{index}": value for index, value in enumerate(values)}


CAP = Statistics(max_token_increase_per_gain=1.0)
SAVE = Statistics(min_token_saving=0.2)
BOTH = Statistics(max_token_increase_per_gain=1.0, min_token_saving=0.2)
# Four cases that gain 1 each (SE 0, score promote) and cost +50%, +50%, +50%, +50%.
PROMOTING = _deltas(1.0, 1.0, 1.0, 1.0)
BAND = _deltas(0.1, -0.1, 0.1, -0.1)  # mean 0, SE > 0: inside the band


# --- the verdict rule ------------------------------------------------------------------------------------

def test_the_rule_is_off_without_a_token_field_even_when_tokens_are_given() -> None:
    tokens = _tokens((100, 900), (100, 900), (100, 900), (100, 900))
    plain = verdict(PROMOTING)
    given = verdict(PROMOTING, tokens=tokens)
    assert given == plain
    assert (given.verdict, given.reasons, given.token_delta, given.token_se) == ("promote", (), None, None)
    assert verdict(BAND, tokens=_tokens((100, 1), (100, 1), (100, 1), (100, 1))).verdict == "inconclusive"


def test_a_promote_whose_token_increase_exceeds_the_cap_per_gain_rejects_with_token_cost() -> None:
    tokens = _tokens((100, 250), (100, 250), (100, 250), (100, 250))  # +150% for a gain of 1.0
    result = verdict(PROMOTING, thresholds=CAP, tokens=tokens)
    assert (result.verdict, result.reasons) == ("reject", ("token-cost",))
    assert result.token_delta == pytest.approx(1.5) and result.token_se == 0.0
    assert result.delta == 1.0


def test_a_promote_inside_the_cap_stays_a_promote_and_reports_the_change() -> None:
    tokens = _tokens((100, 150), (100, 150), (100, 150), (100, 150))
    result = verdict(PROMOTING, thresholds=CAP, tokens=tokens)
    assert (result.verdict, result.reasons) == ("promote", ())
    assert result.token_delta == pytest.approx(0.5)


def test_the_cap_boundary_is_exclusive_and_a_zero_cap_rejects_any_increase() -> None:
    even = _tokens((100, 200), (100, 200), (100, 200), (100, 200))  # +100% = 1.0 * delta
    assert verdict(PROMOTING, thresholds=CAP, tokens=even).verdict == "promote"
    zero = Statistics(max_token_increase_per_gain=0.0)
    assert verdict(PROMOTING, thresholds=zero, tokens=_tokens(*[(100, 101)] * 4)).reasons == ("token-cost",)
    assert verdict(PROMOTING, thresholds=zero, tokens=_tokens(*[(100, 100)] * 4)).verdict == "promote"


def test_a_noisy_token_change_uses_the_mean_and_se_across_cases() -> None:
    tokens = _tokens((100, 100), (100, 200), (100, 100), (100, 200))  # changes 0, 1, 0, 1
    result = verdict(PROMOTING, thresholds=CAP, tokens=tokens)
    assert result.token_delta == pytest.approx(0.5)
    assert result.token_se == pytest.approx(math.sqrt(1 / 3) / 2)


def test_the_change_averages_repeats_per_case_not_per_outcome() -> None:
    # Baseline repeats 50 and 150 average 100; winner repeats 100 and 100 average 100: a change of 0, not a mean of ratios.
    outcomes = [_holdout("baseline", "c0", 0, 50), _holdout("baseline", "c0", 1, 150),
                _holdout("winner", "c0", 0, 100), _holdout("winner", "c0", 1, 100),
                _holdout("baseline", "c1", 0, 50), _holdout("baseline", "c1", 1, 150),
                _holdout("winner", "c1", 0, 100), _holdout("winner", "c1", 1, 100)]
    tokens = _session(CAP, outcomes)._holdout_tokens()  # pyright: ignore[reportPrivateUsage] -- unit test of the private reader
    assert tokens == {"c0": (100.0, 100.0), "c1": (100.0, 100.0)}
    assert verdict(_deltas(1.0, 1.0), thresholds=CAP, tokens=tokens).token_delta == 0.0


def test_a_band_result_with_a_clear_saving_becomes_promote_cheaper() -> None:
    tokens = _tokens((100, 50), (100, 60), (100, 55), (100, 52))
    result = verdict(BAND, thresholds=SAVE, tokens=tokens)
    assert (result.verdict, result.reasons) == ("promote-cheaper", ())
    assert cast(float, result.token_delta) < -0.2


def test_promote_cheaper_never_appears_without_min_token_saving() -> None:
    tokens = _tokens(*[(100, 10)] * 4)
    assert verdict(BAND, thresholds=CAP, tokens=tokens).verdict == "inconclusive"
    assert verdict(BAND, tokens=tokens).verdict == "inconclusive"


def test_promote_cheaper_needs_both_the_share_and_two_token_se() -> None:
    shallow = _tokens(*[(100, 85)] * 4)  # saves 15%, below the 20% share
    assert verdict(BAND, thresholds=SAVE, tokens=shallow).verdict == "inconclusive"
    noisy = _tokens((100, 10), (100, 140), (100, 10), (100, 140))  # mean -0.25, SE 0.375: inside 2 SE
    result = verdict(BAND, thresholds=SAVE, tokens=noisy)
    assert result.token_delta == pytest.approx(-0.25) and result.verdict == "inconclusive"
    exact = _tokens(*[(100, 75)] * 4)  # SE 0, saving exactly the share
    assert verdict(BAND, thresholds=Statistics(min_token_saving=0.25), tokens=exact).verdict == "promote-cheaper"


def test_equal_token_changes_use_the_shared_value_and_a_flat_change_is_not_cheaper() -> None:
    flat = _tokens(*[(100, 100)] * 4)
    assert verdict(BAND, thresholds=Statistics(min_token_saving=0.01), tokens=flat).verdict == "inconclusive"
    up = _tokens(*[(100, 120)] * 4)
    assert verdict(BAND, thresholds=SAVE, tokens=up).verdict == "inconclusive"


def test_floors_do_not_gate_promote_cheaper_but_a_floor_failure_is_outside_the_band() -> None:
    cheap = _tokens(*[(100, 50)] * 4)
    strict = Statistics(min_gain=0.5, min_lower_bound=0.5, min_token_saving=0.2)
    assert verdict(BAND, thresholds=strict, tokens=cheap).verdict == "promote-cheaper"
    demoted = verdict(PROMOTING, thresholds=Statistics(min_gain=5.0, min_token_saving=0.2), tokens=cheap)
    assert (demoted.verdict, demoted.reasons) == ("inconclusive", ("min-gain",))


def test_a_reject_by_score_stays_a_reject_whatever_the_tokens() -> None:
    losing = _deltas(-1.0, -1.0, -1.0, -1.0)
    assert verdict(losing, thresholds=BOTH, tokens=_tokens(*[(100, 10)] * 4)).verdict == "reject"


def test_a_family_breach_beats_promote_cheaper() -> None:
    deltas = {"a1": -1.0, "a2": -1.0, "b1": 1.0, "b2": 1.0}  # mean 0, SE > 0: inside the band
    families = {"a1": "a", "a2": "a", "b1": "b", "b2": "b"}
    cheap = {key: (100.0, 50.0) for key in deltas}
    control = verdict(deltas, thresholds=Statistics(min_token_saving=0.2), families=families, tokens=cheap)
    assert control.verdict == "promote-cheaper"
    result = verdict(deltas, thresholds=Statistics(family_budget=0.0, min_token_saving=0.2),
                     families=families, tokens=cheap)
    assert (result.verdict, result.reasons) == ("reject", ("family-regression:a",))


def test_a_family_breach_follows_the_token_reason_in_rule_order() -> None:
    deltas = {"a1": -0.1, "a2": -0.1, "b1": 3.0, "b2": 3.0, "b3": 3.0, "b4": 3.0}
    families = {"a1": "a", "a2": "a", "b1": "b", "b2": "b", "b3": "b", "b4": "b"}
    costly = {key: (100.0, 900.0) for key in deltas}
    thresholds = Statistics(max_token_increase_per_gain=0.1, family_budget=0.05)
    result = verdict(deltas, thresholds=thresholds, families=families, tokens=costly)
    assert (result.verdict, result.reasons) == ("reject", ("token-cost", "family-regression:a"))


@pytest.mark.parametrize("pairs", [
    [(None, 100), (100, 100), (100, 100), (100, 100)],
    [(100, None), (100, 100), (100, 100), (100, 100)],
    [(0, 100), (100, 100), (100, 100), (100, 100)],
    [(100, math.nan), (100, 100), (100, 100), (100, 100)],
    [(math.inf, 100), (100, 100), (100, 100), (100, 100)]])
def test_unknown_usage_or_a_zero_baseline_turns_a_promote_into_inconclusive(
        pairs: list[tuple[float | None, float | None]]) -> None:
    for thresholds in (CAP, SAVE, BOTH):
        result = verdict(PROMOTING, thresholds=thresholds, tokens=_tokens(*pairs))
        assert (result.verdict, result.reasons) == ("inconclusive", ("unknown-usage",))
        assert (result.token_delta, result.token_se) == (None, None)


def test_unknown_usage_blocks_promote_cheaper_and_names_the_reason() -> None:
    result = verdict(BAND, thresholds=SAVE, tokens=_tokens(*[(0, 0)] * 4))
    assert (result.verdict, result.reasons) == ("inconclusive", ("unknown-usage",))
    quiet = verdict(BAND, thresholds=CAP, tokens=_tokens(*[(0, 0)] * 4))
    assert quiet.reasons == ()


def test_a_family_breach_still_wins_over_unknown_usage() -> None:
    deltas = {"a1": -1.0, "a2": -1.0, "b1": 3.0, "b2": 3.0, "b3": 3.0}
    fams = {"a1": "a", "a2": "a", "b1": "b", "b2": "b", "b3": "b"}
    result = verdict(deltas, thresholds=Statistics(max_token_increase_per_gain=1.0, family_budget=0.0),
                     families=fams, tokens={key: (None, None) for key in deltas})
    assert result.verdict == "reject" and result.reasons[-1] == "family-regression:a"


def test_tokens_need_deltas_keyed_by_the_same_case_ids() -> None:
    with pytest.raises(ValueError, match="tokens need deltas keyed"):
        _ = verdict([1.0, 1.0], thresholds=CAP, tokens=_tokens((1, 1), (1, 1)))
    with pytest.raises(ValueError, match="tokens need deltas keyed"):
        _ = verdict(_deltas(1.0, 1.0), thresholds=CAP, tokens=_tokens((1, 1)))


def test_the_non_finite_and_short_paths_report_the_token_change_without_a_promotion() -> None:
    nan = verdict(_deltas(math.nan, 1.0), thresholds=BOTH, tokens=_tokens((100, 50), (100, 50)))
    assert nan.verdict == "inconclusive" and nan.token_delta == pytest.approx(-0.5)
    one = verdict(_deltas(1.0), thresholds=BOTH, tokens=_tokens((100, 50)))
    assert (one.verdict, one.token_delta, one.token_se) == ("inconclusive", pytest.approx(-0.5), 0.0)


def test_the_gate_record_always_holds_the_token_keys() -> None:
    assert verdict(PROMOTING).data()["token_delta"] is None
    assert verdict(PROMOTING).data()["token_se"] is None
    data = verdict(PROMOTING, thresholds=CAP, tokens=_tokens(*[(100, 150)] * 4)).data()
    assert data["token_delta"] == pytest.approx(0.5) and data["token_se"] == 0.0


def test_defaults_keep_every_verdict_and_the_pinned_simulation() -> None:
    for values in ([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0], [0.0, 0.0], [1.0], [], [1.0, -1.0, 0.1]):
        assert verdict(values, thresholds=DEFAULT_STATISTICS).reasons == ()
    result = simulate()
    assert (result.trials, result.cases, result.rate_2se, result.rate_one_holdout) == (2000, 10, 0.0365, 0.8115)


# --- charged tokens --------------------------------------------------------------------------------------

def _usage(low: int | None, cached: int | None, high: int | None) -> dict[str, int | None]:
    return {"input_tokens": low, "cached_input_tokens": cached, "output_tokens": high}


def test_task_tokens_charge_uncached_input_plus_output_and_ignore_judge_usage() -> None:
    plain = {"usage": _usage(10, 0, 5)}
    cached = {"usage": _usage(1009, 999, 5)}  # `input_tokens` holds the cached input, as the adapters emit it
    judged = {"usage": _usage(110, 0, 55), "task_usage": _usage(10, 0, 5), "judge_usage": _usage(100, 7, 50)}
    assert task_tokens(plain) == task_tokens(cached) == task_tokens(judged) == 15


def test_task_tokens_are_unknown_when_a_count_or_the_usage_is_missing() -> None:
    assert task_tokens({}) is None
    assert task_tokens({"usage": None}) is None
    assert task_tokens({"usage": _usage(None, 0, 5)}) is None
    assert task_tokens({"usage": _usage(10, None, 5)}) is None
    assert task_tokens({"usage": _usage(10, 0, None)}) is None
    assert task_tokens({"usage": _usage(10, 11, 5)}) is None
    assert task_tokens({"usage": _usage(10, 0, 5), "task_usage": None}) is None


# --- selection tie-break ---------------------------------------------------------------------------------

VALIDATION = ["v1", "v2"]


def _outcomes(identity: str, score: float, tokens: int | None) -> list[dict[str, object]]:
    usage = None if tokens is None else _usage(tokens, 0, 0)
    return [{"arm": "search", "split": "validation", "search_candidate": identity, "case_id": case_id,
             "score": score, "usage": usage} for case_id in VALIDATION]


def _changed(files: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(identity="changed", files=files)


def _cases_for(_split: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(identifier=item) for item in VALIDATION]


def _session(statistics: Statistics, outcomes: list[dict[str, object]], kept: dict[str, object] | None = None,
             ) -> _Session:
    """Return a `_Session` with only the attributes that the tie-break reads."""
    session = object.__new__(_Session)
    attributes: dict[str, object] = {
        "lock": threading.RLock(), "statistics": statistics, "outcomes": outcomes,
        "record": {} if kept is None else {"best_validated": kept},
        "seed": SimpleNamespace(identity="seed", editable=("a",), changed=_changed),
        "cases_for": _cases_for,
        "checkpoint": lambda: None}
    for name, value in attributes.items():
        setattr(session, name, value)
    return session


def _candidate(identity: str) -> Candidate:
    return cast(Candidate, cast(object, SimpleNamespace(identity=identity, files={"a": identity})))


validation_case = cast(Case, cast(object, SimpleNamespace(split="validation")))


def _holdout(arm: str, case_id: str, repeat: int, low: int) -> dict[str, object]:
    return {"arm": arm, "split": "holdout", "case_id": case_id, "repeat": repeat, "score": 1.0,
            "task_usage": _usage(low, 0, 0)}


def _kept_after(statistics: Statistics, first: int | None, second: int | None) -> object:
    outcomes = _outcomes("one", 0.5, first) + _outcomes("two", 0.5, second)
    session = _session(statistics, outcomes[:2])
    session._keep_if_best(_candidate("one"), validation_case)  # pyright: ignore[reportPrivateUsage] -- unit test of the private keeper
    session.outcomes.extend(outcomes[2:])
    session._keep_if_best(_candidate("two"), validation_case)  # pyright: ignore[reportPrivateUsage] -- unit test of the private keeper
    return cast(dict[str, object], session.record["best_validated"])["identity"]


def test_keep_if_best_breaks_a_mean_tie_toward_fewer_tokens_only_while_a_token_field_is_set() -> None:
    assert _kept_after(SAVE, 100, 60) == "two"
    assert _kept_after(CAP, 100, 60) == "two"
    assert _kept_after(DEFAULT_STATISTICS, 100, 60) == "one"  # today: the first candidate wins


def test_keep_if_best_keeps_the_first_candidate_for_equal_costlier_or_unknown_tokens() -> None:
    assert _kept_after(SAVE, 100, 100) == "one"
    assert _kept_after(SAVE, 60, 100) == "one"
    assert _kept_after(SAVE, None, 60) == "one"
    assert _kept_after(SAVE, 100, None) == "one"


def test_a_better_mean_still_beats_a_cheaper_tie() -> None:
    outcomes = _outcomes("one", 0.5, 10) + _outcomes("two", 0.6, 9000)
    session = _session(SAVE, outcomes[:2])
    session._keep_if_best(_candidate("one"), validation_case)  # pyright: ignore[reportPrivateUsage] -- unit test of the private keeper
    session.outcomes.extend(outcomes[2:])
    session._keep_if_best(_candidate("two"), validation_case)  # pyright: ignore[reportPrivateUsage] -- unit test of the private keeper
    assert cast(dict[str, object], session.record["best_validated"])["identity"] == "two"


def _winner(statistics: Statistics, seed_tokens: int | None, other_tokens: int | None, other_score: float = 0.5) -> object:
    outcomes = _outcomes("seed", 0.5, seed_tokens) + _outcomes("other", other_score, other_tokens)
    kept: dict[str, object] = {"identity": "other", "mean": other_score, "files": {"a": "other"}}
    session = _session(statistics, outcomes, kept)
    return session._best_validated(VALIDATION).identity  # pyright: ignore[reportPrivateUsage] -- unit test of the private picker


def test_best_validated_breaks_a_mean_tie_toward_fewer_tokens_only_while_a_token_field_is_set() -> None:
    assert _winner(SAVE, 100, 60) == "changed"
    assert _winner(CAP, 100, 60) == "changed"
    assert _winner(DEFAULT_STATISTICS, 100, 60) == "seed"  # today: the seed wins


def test_best_validated_keeps_the_seed_for_equal_costlier_or_unknown_tokens() -> None:
    assert _winner(SAVE, 100, 100) == "seed"
    assert _winner(SAVE, 60, 100) == "seed"
    assert _winner(SAVE, None, 60) == "seed"
    assert _winner(SAVE, 100, None) == "seed"
    assert _winner(SAVE, 100, 60, other_score=0.4) == "seed"
    assert _winner(SAVE, 9000, 60, other_score=0.6) == "changed"


# --- reflection text -------------------------------------------------------------------------------------

TODAY_PROSE = ("Improve only the supplied skill text components. Preserve the helper CLI contract. "
               "Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
               "active voice, present tense, one instruction per sentence, at most 20 words per procedural sentence, "
               "and at most 25 words per descriptive sentence. "
               "Return complete component contents. Do not alter independent checks or permissions.\n")


def test_the_reflection_prompt_is_unchanged_without_a_token_field_and_gains_one_sentence_with_one() -> None:
    body = json.dumps({"candidate": {"SKILL.md": "x"}, "feedback": {}}, default=str)
    plain, schema = _reflection_request("prose", {"SKILL.md": "x"}, {}, ["SKILL.md"])
    assert plain == TODAY_PROSE + body
    assert _reflection_request("prose", {"SKILL.md": "x"}, {}, ["SKILL.md"], cheaper=False) == (plain, schema)
    cheaper, same_schema = _reflection_request("prose", {"SKILL.md": "x"}, {}, ["SKILL.md"], cheaper=True)
    assert cheaper == TODAY_PROSE.replace("permissions.\n", "permissions." + SENTENCE + "\n") + body
    assert same_schema == schema


# --- statistics record, flags, contract ------------------------------------------------------------------

def test_the_record_holds_both_token_keys_and_round_trips() -> None:
    assert DEFAULT_STATISTICS.data()["max_token_increase_per_gain"] is None
    assert DEFAULT_STATISTICS.data()["min_token_saving"] is None
    full = Statistics(max_token_increase_per_gain=0.5, min_token_saving=0.25)
    assert Statistics.parse(cast(object, json.loads(json.dumps(full.data())))) == full
    assert not DEFAULT_STATISTICS.token_rule and full.token_rule
    assert Statistics(min_token_saving=0.5).token_rule and Statistics(max_token_increase_per_gain=0.0).token_rule


def test_a_record_without_the_token_keys_is_refused() -> None:
    base = DEFAULT_STATISTICS.data()
    del base["min_token_saving"]
    with pytest.raises(ValueError, match="lacks fields: min_token_saving"):
        _ = Statistics.parse(base)


def test_the_flag_registry_lists_both_token_flags() -> None:
    assert Statistics.flags()[-2:] == ("max_token_increase_per_gain", "min_token_saving")


@pytest.mark.parametrize("value", [0.0, 1.0, -0.1, 1.5, True, "0.5", math.nan, math.inf, 10**400])
def test_min_token_saving_accepts_only_a_number_strictly_between_0_and_1(value: object) -> None:
    with pytest.raises(ValueError, match="min_token_saving must be null or a number above 0 and below 1"):
        _ = Statistics.declared({"min_token_saving": value})
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics={"min_token_saving": value}), "skill")
    assert caught.value.code == "contract-statistics-invalid"


@pytest.mark.parametrize("value", [-0.1, True, "1", math.nan, math.inf, 10**400])
def test_max_token_increase_per_gain_accepts_only_a_finite_number_of_at_least_0(value: object) -> None:
    with pytest.raises(CodedError) as caught:
        _ = parse(_document(statistics={"max_token_increase_per_gain": value}), "skill")
    assert caught.value.code == "contract-statistics-invalid"


def test_a_contract_accepts_both_token_fields_and_null() -> None:
    contract = parse(_document(statistics={"max_token_increase_per_gain": 2, "min_token_saving": 0.5}), "skill")
    resolved = resolve_statistics(contract, {})
    assert (resolved.max_token_increase_per_gain, resolved.min_token_saving) == (2.0, 0.5)
    nulled = parse(_document(statistics={"max_token_increase_per_gain": None, "min_token_saving": None}), "skill")
    assert not resolve_statistics(nulled, {}).token_rule
    assert Statistics.declared({"min_token_saving": 0.999}) == {"min_token_saving": 0.999}
    assert Statistics.declared({"max_token_increase_per_gain": 0}) == {"max_token_increase_per_gain": 0.0}


def test_a_flag_beats_the_contract_token_value() -> None:
    contract = parse(_document(statistics={"min_token_saving": 0.5}), "skill")
    assert resolve_statistics(contract, {"min_token_saving": 0.1}).min_token_saving == 0.1
    assert resolve_statistics(contract, {}).min_token_saving == 0.5


def test_a_contract_without_token_fields_hashes_as_before() -> None:
    floors = parse(_document(statistics={"min_gain": 0.5}), "skill")
    with_tokens = parse(_document(statistics={"min_gain": 0.5, "min_token_saving": 0.5}), "skill")
    assert floors.data()["statistics"] == {"min_gain": 0.5}
    assert with_tokens.identity != floors.identity


def test_a_bad_token_flag_is_refused_by_name(tmp_path: Path, make_target: Callable[..., Path]) -> None:
    target = make_target(tmp_path)
    for bad in (0.0, 1.0, -0.5, math.nan, True):
        with pytest.raises(ValueError, match="--min-token-saving must be null or a number above 0 and below 1"):
            _ = run(target, tmp_path / "run", MODEL, statistics={"min_token_saving": bad})
    for bad in (-1.0, math.inf, True):
        with pytest.raises(ValueError, match="--max-token-increase-per-gain"):
            _ = run(target, tmp_path / "run", MODEL, statistics={"max_token_increase_per_gain": bad})


def test_the_cli_passes_both_token_flags_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake(*_args: object, **kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {}

    monkeypatch.setattr("skillz_experiments._cli.run_workflow", fake)
    base = ["run", "--target", str(tmp_path / "skill"), "--out", str(tmp_path / "run"), "--model", MODEL]
    assert main([*base, "--max-token-increase-per-gain=1.5", "--min-token-saving=0.25"]) == 0
    assert seen["statistics"] == {"max_token_increase_per_gain": 1.5, "min_token_saving": 0.25}
    assert main([*base, "--min-token-saving=0.25"]) == 0
    assert seen["statistics"] == {"min_token_saving": 0.25}


# --- workflow: provider with usage -----------------------------------------------------------------------

class _Priced:
    """A fake provider. The `improved` candidate scores `GAIN` on every case and uses `IMPROVED` task tokens.

    `HOLDOUT_GAIN`, when set, replaces `GAIN` on the holdout.
    """

    GAIN: float = 1.0
    HOLDOUT_GAIN: float | None = None
    BASE: int = 100
    IMPROVED: int = 150
    JUDGE: int = 0
    CACHED: int = 0
    KNOWN: bool = True
    prompts: list[str] = []

    def __init__(self, _model: str, _budget: Budget, _checkpoint: Callable[[], None]) -> None:
        pass

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        improved = "improved" in candidate.files["SKILL.md"]
        gain = self.HOLDOUT_GAIN if holdout and self.HOLDOUT_GAIN is not None else self.GAIN
        result: dict[str, object] = {"score": gain if improved else 0.0, "candidate_hash": candidate.identity,
                                     "case_hash": case.identifier}
        if self.KNOWN:
            low = self.IMPROVED if improved else self.BASE
            task = _usage(low + self.CACHED, self.CACHED, 0)  # `input_tokens` includes the cached input
            result.update({"usage": _usage(low + self.CACHED + self.JUDGE, self.CACHED, 0),
                           "task_usage": task, "judge_usage": _usage(self.JUDGE, 0, 0)})
        return result

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del candidate, case, holdout
        assert schema is not None
        self.prompts.append(prompt)
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def _priced(**attributes: object) -> type[_Priced]:
    return cast(type[_Priced], type("Priced", (_Priced,), {**attributes, "prompts": []}))


def _finish(tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
            approvals: Callable[..., tuple[str, int]], provider: type[_Priced],
            statistics: dict[str, float] | None, name: str = "run") -> dict[str, object]:
    target, out = make_target(tmp_path) if not (tmp_path / "echo-skill").exists() else tmp_path / "echo-skill", tmp_path / name
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    return run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=provider,
               statistics=statistics)


def _gate(result: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], result["gate"])


def test_a_costly_winner_is_rejected_for_token_cost_and_summary_and_export_show_the_change(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    provider = _priced(IMPROVED=300)  # +200% tokens for a gain of 1.0
    result = _finish(tmp_path, make_target, write_draft, approvals, provider, {"max_token_increase_per_gain": 1.0})
    gate = _gate(result)
    assert (gate["verdict"], gate["reasons"]) == ("reject", ["token-cost"])
    assert gate["token_delta"] == pytest.approx(2.0) and gate["token_se"] == 0.0
    _ = export(tmp_path / "run", tmp_path / "export")
    report = cast(dict[str, object], json.loads((tmp_path / "export" / "report.json").read_text()))
    assert report["gate"] == gate
    saved = cast(dict[str, object], read(tmp_path / "run" / "run.json")["statistics"])
    assert saved["max_token_increase_per_gain"] == 1.0
    assert saved["min_token_saving"] is None


def test_a_winner_inside_the_cap_promotes(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    gate = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(IMPROVED=120),
                         {"max_token_increase_per_gain": 1.0}))
    assert (gate["verdict"], gate["reasons"]) == ("promote", [])
    assert gate["token_delta"] == pytest.approx(0.2)


def test_the_rule_is_off_by_default_and_the_gate_keeps_its_verdict(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    provider = _priced(IMPROVED=900)
    result = _finish(tmp_path, make_target, write_draft, approvals, provider, None)
    gate = _gate(result)
    assert (gate["verdict"], gate["reasons"], gate["token_delta"], gate["token_se"]) == ("promote", [], None, None)
    assert all(SENTENCE not in prompt for prompt in provider.prompts) and provider.prompts


def test_judge_usage_and_cached_input_do_not_change_the_token_change(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    flags = {"max_token_increase_per_gain": 1.0}
    plain = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(IMPROVED=120), flags, "one"))
    loud = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(IMPROVED=120, JUDGE=5000, CACHED=777),
                         flags, "two"))
    assert plain["token_delta"] == loud["token_delta"] and plain["token_se"] == loud["token_se"]
    assert plain["verdict"] == loud["verdict"] == "promote"


def test_missing_usage_turns_a_promote_into_unknown_usage(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    gate = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(KNOWN=False),
                         {"max_token_increase_per_gain": 1.0}))
    assert (gate["verdict"], gate["reasons"]) == ("inconclusive", ["unknown-usage"])
    assert (gate["token_delta"], gate["token_se"]) == (None, None)


def test_a_cheaper_winner_without_a_holdout_gain_promotes_cheaper_only_with_min_token_saving(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    cheap = {"HOLDOUT_GAIN": 0.0, "IMPROVED": 50}  # wins the search, ties the baseline on the holdout
    off = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(**cheap), None, "off"))
    assert (off["verdict"], off["reasons"], off["token_delta"]) == ("inconclusive", [], None)
    capped = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(**cheap),
                           {"max_token_increase_per_gain": 1.0}, "capped"))
    assert capped["verdict"] == "inconclusive" and capped["token_delta"] == pytest.approx(-0.5)
    on = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(**cheap), {"min_token_saving": 0.3}, "on"))
    assert (on["verdict"], on["reasons"]) == ("promote-cheaper", [])
    assert on["token_delta"] == pytest.approx(-0.5)
    shallow = _gate(_finish(tmp_path, make_target, write_draft, approvals, _priced(HOLDOUT_GAIN=0.0, IMPROVED=90),
                            {"min_token_saving": 0.3}, "shallow"))
    assert shallow["verdict"] == "inconclusive"


def test_the_reflection_prompt_carries_the_token_sentence_while_a_token_field_is_set(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    provider = _priced()
    _ = _finish(tmp_path, make_target, write_draft, approvals, provider, {"min_token_saving": 0.5})
    assert provider.prompts and all(SENTENCE in prompt for prompt in provider.prompts)


def test_a_resume_flag_must_match_the_frozen_token_fields(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    flags = {"max_token_increase_per_gain": 1.0, "min_token_saving": 0.5}
    with pytest.raises(Stop) as prepared:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls, statistics=flags)
    assert prepared.value.code == "live-required"
    for name, other in (("max_token_increase_per_gain", 2.0), ("min_token_saving", 0.25)):
        with pytest.raises(CodedError) as caught:
            _ = run(target, out, MODEL, statistics={name: other})
        assert caught.value.code == "run-config-differs" and "--" + name.replace("_", "-") in str(caught.value)
    with pytest.raises(Stop) as same:
        _ = run(target, out, MODEL, statistics=flags)
    assert same.value.code == "live-required"
    frozen = cast(dict[str, object], read(out / "run.json")["statistics"])
    assert (frozen["max_token_increase_per_gain"], frozen["min_token_saving"]) == (1.0, 0.5)
