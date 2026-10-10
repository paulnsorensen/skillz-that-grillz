"""Case-clustered paired holdout gate and its false-promotion simulation."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Literal, cast

VerdictName = Literal["promote", "promote-cheaper", "inconclusive", "reject"]


def threshold(value: object) -> float | None:
    """Return `value` as a float when it is a finite number of at least 0; else return None.

    A bool, a non-number, an int too large for a float, a non-finite number, and a negative number give None.
    """
    if type(value) not in (int, float):
        return None
    try:
        number = float(cast(float, value))
    except OverflowError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number + 0.0  # turns -0.0 into 0.0 so both hash alike


def _saving(value: object) -> float | None:
    """Return `value` as a float when it lies strictly between 0 and 1; else return None."""
    number = threshold(value)
    return number if number is not None and 0 < number < 1 else None


def _budget_map(name: str, value: object) -> dict[str, float]:
    """Check a map of family name to budget. Raise `ValueError` for a shape problem."""
    if not isinstance(value, dict):
        raise ValueError(f"statistics {name} must be an object of numbers")
    result: dict[str, float] = {}
    for key, number in cast(dict[object, object], value).items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"statistics {name} keys must be nonempty strings")
        checked = threshold(number)
        if checked is None:
            raise ValueError(f"statistics {name} {key} must be a finite number of at least 0")
        result[key] = checked
    return result


@dataclass(frozen=True)
class Statistics:
    """Resolved promotion thresholds. The defaults reproduce the built-in 2*SE rule.

    Every field except `min_token_saving` only tightens the rule. `min_token_saving` also adds `promote-cheaper`.
    That verdict needs no observed loss and claims no gain. The SE multiplier stays 2. `min_gain` and
    `min_lower_bound` are floors of at least 0.
    A field with `flag` metadata also has a CLI flag. A field with `nullable` metadata may be None, which
    turns its rule off. A field with `map` metadata holds a name-to-number map and is contract-only.
    `max_token_increase_per_gain` and `min_token_saving` set the token rule, which is off while both are None.
    `min_token_saving` is a saving share strictly between 0 and 1.
    A field with `check` metadata sets its own value check: a function that returns the checked float or None.
    The default check is `threshold`. The `rule` metadata then names the accepted values in the error message.
    `declared`, `parse`, and `data` check the shape of a map, not its agreement with any run record.
    """

    min_gain: float = field(default=0.0, metadata={"flag": True})
    min_lower_bound: float = field(default=0.0, metadata={"flag": True})
    family_budget: float | None = field(default=None, metadata={"flag": True, "nullable": True})
    family_budgets: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({}), hash=False,
                                                metadata={"map": True})
    max_token_increase_per_gain: float | None = field(default=None, metadata={"flag": True, "nullable": True})
    min_token_saving: float | None = field(default=None, metadata={
        "flag": True, "nullable": True, "check": _saving, "rule": "null or a number above 0 and below 1"})

    def __post_init__(self) -> None:
        object.__setattr__(self, "family_budgets", MappingProxyType(dict(sorted(self.family_budgets.items()))))

    @property
    def token_rule(self) -> bool:
        """Whether a token field is set, which turns the token rule on."""
        return self.max_token_increase_per_gain is not None or self.min_token_saving is not None

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(item.name for item in fields(cls))

    @classmethod
    def flags(cls) -> tuple[str, ...]:
        return tuple(item.name for item in fields(cls) if item.metadata.get("flag"))

    @classmethod
    def declared(cls, value: object) -> dict[str, object]:
        """Check a partial mapping of thresholds. Raise `ValueError` for a shape problem."""
        if not isinstance(value, dict):
            raise ValueError("statistics must be an object")
        item = cast(dict[str, object], value)
        unknown = sorted(set(item) - set(cls.names()))
        if unknown:
            raise ValueError(f"statistics has unknown fields: {', '.join(unknown)}; allowed: {', '.join(cls.names())}")
        metadata = {entry.name: entry.metadata for entry in fields(cls)}
        result: dict[str, object] = {}
        for name, number in item.items():
            if metadata[name].get("map"):
                result[name] = _budget_map(name, number)
            elif number is None and metadata[name].get("nullable"):
                result[name] = None
            else:
                check = cast(Callable[[object], float | None], metadata[name].get("check", threshold))
                checked = check(number)
                if checked is None:
                    rule = cast(str, metadata[name].get("rule", "a finite number of at least 0"))
                    raise ValueError(f"statistics {name} must be {rule}")
                result[name] = checked
        return result

    @classmethod
    def parse(cls, value: object) -> Statistics:
        """Check a full mapping of thresholds: exactly the fields of this class. Raise `ValueError` otherwise."""
        checked = cls.declared(value)
        missing = sorted(set(cls.names()) - set(checked))
        if missing:
            raise ValueError(f"statistics lacks fields: {', '.join(missing)}")
        return cast(Callable[..., Statistics], cls)(**checked)

    def data(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for item in fields(self):
            value = cast(object, getattr(self, item.name))
            result[item.name] = dict(cast(Mapping[str, float], value)) if item.metadata.get("map") else value
        return result


DEFAULT_STATISTICS = Statistics()


@dataclass(frozen=True)
class Verdict:
    verdict: VerdictName
    delta: float
    se: float
    cases: int
    reasons: tuple[str, ...] = ()
    skipped_families: tuple[str, ...] = ()
    token_delta: float | None = None
    token_se: float | None = None

    def data(self) -> dict[str, object]:
        """Return the gate keys of the run record, with each tuple as a list."""
        return {"verdict": self.verdict, "delta": self.delta, "se": self.se, "cases": self.cases,
                "reasons": list(self.reasons), "skipped_families": list(self.skipped_families),
                "token_delta": self.token_delta, "token_se": self.token_se}


@dataclass(frozen=True)
class Simulation:
    """False-promotion rates under no true effect.

    With at least 2 cases, the split gate spends no more calls than the one-holdout rule.
    """

    trials: int
    cases: int
    rate_2se: float
    rate_one_holdout: float


def case_deltas(
    baseline: Mapping[str, Sequence[float]], winner: Mapping[str, Sequence[float]]
) -> dict[str, float]:
    """Return the winner-minus-baseline mean delta per case, averaged over repeats."""
    if baseline.keys() != winner.keys():
        raise ValueError("baseline and winner must score the same case ids")
    deltas: dict[str, float] = {}
    for case_id, base in baseline.items():
        won = winner[case_id]
        if not base or not won:
            raise ValueError(f"case {case_id} has no repeat scores")
        deltas[case_id] = statistics.fmean(won) - statistics.fmean(base)
    return deltas


def _family_rule(deltas: Mapping[str, float], families: Mapping[str, str] | None,
                 thresholds: Statistics) -> tuple[list[str], tuple[str, ...]]:
    """Return the breached families and the skipped families, each sorted by family name.

    The rule skips a family with fewer than 2 holdout cases, whatever its budget. A family with no budget is off.
    """
    if families is None:
        return [], ()
    grouped: dict[str, list[float]] = {}
    for case_id, value in deltas.items():
        if case_id in families:
            grouped.setdefault(families[case_id], []).append(value)
    breached: list[str] = []
    skipped: list[str] = []
    for name in sorted(grouped):
        values = grouped[name]
        if len(values) < 2:
            skipped.append(name)
            continue
        budget = thresholds.family_budgets.get(name, thresholds.family_budget)
        if budget is None or not all(math.isfinite(value) for value in values):
            continue
        se = statistics.stdev(values) / math.sqrt(len(values))
        if (values[0] < -budget) if se == 0 else (statistics.fmean(values) + 2 * se < -budget):
            breached.append(name)
    return breached, tuple(skipped)


def _token_change(tokens: Mapping[str, tuple[float | None, float | None]]) -> tuple[float, float] | None:
    """Return the mean and the SE across cases of `mean(winner)/mean(baseline) - 1`, or None when usage is unknown.

    A case is unknown when either mean is missing or not finite, when its baseline mean is not above 0, or when
    its winner mean is negative. One unknown case makes the whole change unknown.
    The SE is the sample standard deviation of the case changes over the square root of the case count.
    One case gives an SE of 0.
    """
    changes: list[float] = []
    for baseline, winner in tokens.values():
        if baseline is None or winner is None or not math.isfinite(baseline) or not math.isfinite(winner):
            return None
        if baseline <= 0 or winner < 0:
            return None
        change = winner / baseline - 1
        if not math.isfinite(change):
            return None
        changes.append(change)
    if not changes:
        return None
    try:
        se = statistics.stdev(changes) / math.sqrt(len(changes)) if len(changes) > 1 else 0.0
        mean = statistics.fmean(changes)
    except OverflowError:
        return None
    return (mean, se) if math.isfinite(mean) and math.isfinite(se) else None


def close(first: float, second: float) -> bool:
    """Return whether two floats are equal within a relative 1e-9 or an absolute 1e-12.

    Only the token threshold checks and the workflow ties use this. The score rule, the floors, and the family
    rule compare exactly, as ADR-002 and the pinned false-promotion rates require.
    """
    return math.isclose(first, second, rel_tol=1e-9, abs_tol=1e-12)


def _exceeds(value: float, limit: float) -> bool:
    """Return whether `value` is above `limit` and not close to it."""
    return value > limit and not close(value, limit)


def _reaches(value: float, limit: float) -> bool:
    """Return whether `value` is at least `limit` or close to it."""
    return value >= limit or close(value, limit)


def verdict(deltas: Mapping[str, float] | Sequence[float], *, thresholds: Statistics = DEFAULT_STATISTICS,
            families: Mapping[str, str] | None = None,
            tokens: Mapping[str, tuple[float | None, float | None]] | None = None) -> Verdict:
    """Promote when the mean case delta exceeds twice its standard error and passes the floors.

    The floors only tighten the rule: a `promote` that misses `min_gain` or `min_lower_bound` becomes
    `inconclusive`, and `reasons` names each floor that failed.
    `families` maps a case id to its family name and needs deltas keyed by case id; else this raises `ValueError`.
    A family whose mean delta plus twice its standard error is below the negative budget turns any verdict
    into `reject`, and `reasons` gets `family-regression:<name>`.
    `skipped_families` lists each family that the rule skipped, on every path.
    `tokens` maps each case id to its (baseline, winner) mean task tokens; it needs deltas keyed by the same case ids.
    The token rule runs only when `max_token_increase_per_gain` or `min_token_saving` is set. It runs after the
    floors and before the families:
    a score `promote` whose mean token change exceeds `max_token_increase_per_gain * delta` becomes `reject`
    with reason `token-cost`;
    a score `inconclusive` (the delta is inside the band, from -2*se to 2*se) becomes `promote-cheaper` when
    `min_token_saving` is set, the delta is at least 0, the tokens drop by more than twice the token SE, and the
    drop is at least `min_token_saving`. The floors do not gate `promote-cheaper`.
    Unknown usage turns a `promote` into `inconclusive` with reason `unknown-usage`. It adds the same reason to
    a band result when `min_token_saving` is set. An active token rule with no `tokens` is unknown usage.
    `token_delta` and `token_se` report the change while the rule runs and the usage is known.
    """
    if families is not None and not isinstance(deltas, Mapping):
        raise ValueError("families need deltas keyed by case id")
    if tokens is not None and (not isinstance(deltas, Mapping) or tokens.keys() != deltas.keys()):
        raise ValueError("tokens need deltas keyed by the same case ids")
    values = list(deltas.values()) if isinstance(deltas, Mapping) else list(deltas)
    count = len(values)
    breached, skipped = _family_rule(deltas if isinstance(deltas, Mapping) else {}, families, thresholds)
    limit, saving = thresholds.max_token_increase_per_gain, thresholds.min_token_saving
    active = thresholds.token_rule
    change = _token_change(tokens) if active and tokens is not None else None
    token_delta, token_se = change if change is not None else (None, None)
    if not all(math.isfinite(value) for value in values):
        return Verdict("inconclusive", math.nan, math.nan, count, skipped_families=skipped,
                       token_delta=token_delta, token_se=token_se)
    if count < 2:
        return Verdict("inconclusive", statistics.fmean(values) if values else 0.0, 0.0, count,
                       skipped_families=skipped, token_delta=token_delta, token_se=token_se)
    delta = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(count)
    if se == 0:
        if all(value > 0 for value in values):
            name: VerdictName = "promote"
        elif all(value < 0 for value in values):
            name = "reject"
        else:
            name = "inconclusive"
    elif delta > 2 * se:
        name = "promote"
    elif delta < -2 * se:
        name = "reject"
    else:
        name = "inconclusive"
    in_band = name == "inconclusive"
    reasons: list[str] = []
    if name == "promote":
        if not delta >= thresholds.min_gain:
            reasons.append("min-gain")
        if not delta > 2 * se + thresholds.min_lower_bound:
            reasons.append("lower-bound")
        if reasons:
            name = "inconclusive"
    if active:
        if name == "promote" and change is None:
            name = "inconclusive"
            reasons.append("unknown-usage")
        elif (name == "promote" and limit is not None and token_delta is not None
              and _exceeds(token_delta, limit * delta)):
            name = "reject"
            reasons.append("token-cost")
        elif in_band and saving is not None:
            if token_delta is None or token_se is None:
                reasons.append("unknown-usage")
            elif delta >= 0 and _exceeds(-token_delta, 2 * token_se) and _reaches(-token_delta, saving):
                name = "promote-cheaper"
    if breached:
        name = "reject"
        reasons.extend(f"family-regression:{family}" for family in breached)
    return Verdict(name, delta, se, count, tuple(reasons), skipped, token_delta, token_se)


def _scores(rng: random.Random, chances: Sequence[float], repeats: int) -> list[float]:
    draw = rng.random
    return [sum(draw() < chance for _ in range(repeats)) / repeats for chance in chances]


def _chances(rng: random.Random, base: Sequence[float], spread: float) -> list[float]:
    return [min(1.0, max(0.0, rng.gauss(chance, spread))) for chance in base]


def _trial(rng: random.Random, cases: int, repeats: int, candidates: int) -> tuple[bool, bool]:
    """Run one null trial; return whether each gate falsely promotes."""
    arms = candidates + 1
    validation = max(1, (arms - 2) * cases // arms)

    def world(size: int) -> tuple[list[float], list[list[float]]]:
        base = [rng.betavariate(2, 2) for _ in range(size)]
        return base, [_chances(rng, base, 0.10) for _ in range(candidates)]

    _, tries = world(validation)
    means = [statistics.fmean(_scores(rng, chances, repeats)) for chances in tries]
    best = max(range(candidates), key=means.__getitem__)
    base, tries = world(cases)
    base_scores = _scores(rng, base, repeats)
    win_scores = _scores(rng, tries[best], repeats)
    split = verdict([w - b for w, b in zip(win_scores, base_scores)]).verdict == "promote"

    base, tries = world(cases)
    base_scores = _scores(rng, base, repeats)
    candidate_scores = [_scores(rng, chances, repeats) for chances in tries]
    best_mean = max(statistics.fmean(scores) for scores in candidate_scores)
    return split, best_mean > statistics.fmean(base_scores)


def simulate(
    seed: int = 20261006,
    trials: int = 2000,
    cases: int = 10,
    repeats: int = 3,
    candidates: int = 5,
) -> Simulation:
    """Estimate false-promotion rates when no candidate has a true effect.

    The split gate selects on a validation set and gates on a separate holdout of
    `cases` cases. The one-holdout rule selects and gates on one set of `cases`
    cases. The one-holdout arm models skill-creator's select-and-gate rule: it
    promotes when the best candidate mean beats the baseline, with no 2*SE test.
    The split gate scores only the candidates on validation, so it spends
    (candidates * validation + 2 * cases) * repeats calls. For `candidates >= 2` and
    `cases >= 2` that is at most the one-holdout spend of
    (candidates + 1) * cases * repeats, so the comparison favors the one-holdout
    rule. The gate cannot promote with fewer than 2 cases, so a smaller holdout
    reports a false rate of zero without a test.
    Raise `ValueError` when `trials < 1`, `cases < 2`, `repeats < 1`, or `candidates < 2`.
    """
    if trials < 1:
        raise ValueError("simulate needs at least 1 trial")
    if cases < 2:
        raise ValueError("simulate needs at least 2 cases")
    if repeats < 1:
        raise ValueError("simulate needs at least 1 repeat")
    if candidates < 2:
        raise ValueError("simulate needs at least 2 candidates")
    rng = random.Random(seed)
    split = single = 0
    for _ in range(trials):
        won_split, won_single = _trial(rng, cases, repeats, candidates)
        split += won_split
        single += won_single
    return Simulation(trials, cases, split / trials, single / trials)
