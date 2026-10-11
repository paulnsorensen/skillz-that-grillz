from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from skillz_experiments.gate._rules import Verdict, verdict


@dataclass(frozen=True)
class Simulation:
    """False-promotion rates and counts under no true effect.

    With at least 2 cases, the split gate spends no more calls than the one-holdout rule.
    Each rate equals its count divided by `trials`.
    """

    trials: int
    cases: int
    rate_2se: float
    rate_one_holdout: float
    false_promotions_2se: int
    false_promotions_one_holdout: int


def _scores(rng: random.Random, chances: Sequence[float], repeats: int) -> list[float]:
    draw = rng.random
    return [sum(draw() < chance for _ in range(repeats)) / repeats for chance in chances]


def _chances(rng: random.Random, base: Sequence[float], spread: float) -> list[float]:
    return [min(1.0, max(0.0, rng.gauss(chance, spread))) for chance in base]


def _trial(rng: random.Random, cases: int, repeats: int, candidates: int,
           rule: Callable[[list[float]], Verdict]) -> tuple[bool, bool]:
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
    split = rule([w - b for w, b in zip(win_scores, base_scores)]).verdict == "promote"

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
    *,
    rule: Callable[[list[float]], Verdict] = verdict,
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
    `rule` replaces the split gate's verdict function; tests inject a weakened gate with it.
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
        won_split, won_single = _trial(rng, cases, repeats, candidates, rule)
        split += won_split
        single += won_single
    return Simulation(trials, cases, split / trials, single / trials, split, single)
