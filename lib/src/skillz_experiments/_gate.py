"""Case-clustered paired holdout gate and its false-promotion simulation."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

VerdictName = Literal["promote", "inconclusive", "reject"]


@dataclass(frozen=True)
class Verdict:
    verdict: VerdictName
    delta: float
    se: float
    cases: int


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


def verdict(deltas: Mapping[str, float] | Sequence[float]) -> Verdict:
    """Promote when the mean case delta exceeds twice its standard error."""
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
    return Verdict(name, delta, se, count)


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
    rule. At `cases == 1` the validation floor of one case breaks this bound.
    Raise `ValueError` when `candidates < 2`.
    """
    if candidates < 2:
        raise ValueError("simulate needs at least 2 candidates")
    rng = random.Random(seed)
    split = single = 0
    for _ in range(trials):
        won_split, won_single = _trial(rng, cases, repeats, candidates)
        split += won_split
        single += won_single
    return Simulation(trials, cases, split / trials, single / trials)
