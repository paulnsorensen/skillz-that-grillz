"""Case-clustered paired holdout gate and its false-promotion simulation."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Literal, cast

VerdictName = Literal["promote", "inconclusive", "reject"]


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


@dataclass(frozen=True)
class Statistics:
    """Resolved promotion thresholds. The defaults reproduce the built-in 2*SE rule.

    These thresholds are floors: each one only tightens the rule.
    The SE multiplier stays 2 and every floor is at least 0.
    A field with `flag` metadata also has a CLI flag. `declared`, `parse`, and `data` check the shape of a map,
    not its agreement with any run record.
    """

    min_gain: float = field(default=0.0, metadata={"flag": True})
    min_lower_bound: float = field(default=0.0, metadata={"flag": True})

    @classmethod
    def names(cls) -> tuple[str, ...]:
        return tuple(item.name for item in fields(cls))

    @classmethod
    def flags(cls) -> tuple[str, ...]:
        return tuple(item.name for item in fields(cls) if item.metadata.get("flag"))

    @classmethod
    def declared(cls, value: object) -> dict[str, float]:
        """Check a partial mapping of thresholds. Raise `ValueError` for a shape problem."""
        if not isinstance(value, dict):
            raise ValueError("statistics must be an object")
        item = cast(dict[str, object], value)
        unknown = sorted(set(item) - set(cls.names()))
        if unknown:
            raise ValueError(f"statistics has unknown fields: {', '.join(unknown)}; allowed: {', '.join(cls.names())}")
        result: dict[str, float] = {}
        for name, number in item.items():
            checked = threshold(number)
            if checked is None:
                raise ValueError(f"statistics {name} must be a finite number of at least 0")
            result[name] = checked
        return result

    @classmethod
    def parse(cls, value: object) -> Statistics:
        """Check a full mapping of thresholds: exactly the fields of this class. Raise `ValueError` otherwise."""
        checked = cls.declared(value)
        missing = sorted(set(cls.names()) - set(checked))
        if missing:
            raise ValueError(f"statistics lacks fields: {', '.join(missing)}")
        return cls(**checked)

    def data(self) -> dict[str, float]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


DEFAULT_STATISTICS = Statistics()


@dataclass(frozen=True)
class Verdict:
    verdict: VerdictName
    delta: float
    se: float
    cases: int
    reasons: tuple[str, ...] = ()


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


def verdict(deltas: Mapping[str, float] | Sequence[float], *, thresholds: Statistics = DEFAULT_STATISTICS) -> Verdict:
    """Promote when the mean case delta exceeds twice its standard error and passes the floors.

    The floors only tighten the rule: a `promote` that misses `min_gain` or `min_lower_bound` becomes
    `inconclusive`, and `reasons` names each floor that failed.
    """
    values = list(deltas.values()) if isinstance(deltas, Mapping) else list(deltas)
    count = len(values)
    if not all(math.isfinite(value) for value in values):
        return Verdict("inconclusive", math.nan, math.nan, count)
    if count < 2:
        return Verdict("inconclusive", statistics.fmean(values) if values else 0.0, 0.0, count)
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
    reasons: list[str] = []
    if name == "promote":
        if not delta >= thresholds.min_gain:
            reasons.append("min-gain")
        if not delta > 2 * se + thresholds.min_lower_bound:
            reasons.append("lower-bound")
        if reasons:
            name = "inconclusive"
    return Verdict(name, delta, se, count, tuple(reasons))


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
