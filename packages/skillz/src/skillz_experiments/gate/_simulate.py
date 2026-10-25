from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from skillz_experiments.gate._rules import Verdict, verdict
from skillz_experiments.gate._statistics import Statistics, holdout_deltas


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


POWER_EFFECTS = (0.1, 0.2, 0.3)


@dataclass(frozen=True)
class Power:
    """Promote rate of the production gate when the candidate has a true effect.

    The candidate chance on each case is `clip(gauss(base + effect, 0.10))`: `effect` shifts the baseline chance,
    then the per-case spread and the clip to [0, 1] apply. The clip lowers the realised mean gain below `effect`.
    `rate` equals `promotions / trials`.
    """

    effect: float
    trials: int
    cases: int
    promotions: int
    rate: float


def _draws(rng: random.Random, chances: Sequence[float], repeats: int) -> list[list[float]]:
    draw = rng.random
    return [[float(draw() < chance) for _ in range(repeats)] for chance in chances]


def _scores(rng: random.Random, chances: Sequence[float], repeats: int) -> list[float]:
    return [sum(case) / repeats for case in _draws(rng, chances, repeats)]


def _floored(base: list[list[float]], win: list[list[float]]) -> Verdict:
    """Return the verdict of the production gate: deltas and the repeat-noise SE floor."""
    paired = holdout_deltas({f"{index:06d}": case for index, case in enumerate(base)},
                            {f"{index:06d}": case for index, case in enumerate(win)})
    return verdict(list(paired.deltas.values()), se_floor=paired.se_floor, planned=len(base))


def _chances(rng: random.Random, base: Sequence[float], spread: float) -> list[float]:
    return [min(1.0, max(0.0, rng.gauss(chance, spread))) for chance in base]


def _world(rng: random.Random, size: int, candidates: int, loss: float = 0.0) -> tuple[list[float], list[list[float]]]:
    """Draw `size` baseline chances and, per candidate, chances that sit `loss` below them before the spread."""
    base = [rng.betavariate(2, 2) for _ in range(size)]
    shifted = [max(0.0, chance - loss) for chance in base] if loss else base
    return base, [_chances(rng, shifted, 0.10) for _ in range(candidates)]


def _selected(rng: random.Random, cases: int, repeats: int, candidates: int, loss: float = 0.0) -> tuple[list[float], list[float]]:
    """Pick the best candidate on a validation set, then return the holdout chances of the baseline and that candidate."""
    arms = candidates + 1
    validation = max(1, (arms - 2) * cases // arms)
    _, tries = _world(rng, validation, candidates, loss)
    means = [statistics.fmean(_scores(rng, chances, repeats)) for chances in tries]
    best = max(range(candidates), key=means.__getitem__)
    base, tries = _world(rng, cases, candidates, loss)
    return base, tries[best]


def _trial(rng: random.Random, cases: int, repeats: int, candidates: int,
           rule: Callable[[list[float]], Verdict] | None) -> tuple[bool, bool]:
    """Run one null trial; return whether each gate falsely promotes."""
    base, chosen = _selected(rng, cases, repeats, candidates)
    base_draws = _draws(rng, base, repeats)
    win_draws = _draws(rng, chosen, repeats)
    if rule is None:
        result = _floored(base_draws, win_draws)
    else:
        result = rule([sum(w) / repeats - sum(b) / repeats for w, b in zip(win_draws, base_draws)])
    split = result.verdict == "promote"

    base, tries = _world(rng, cases, candidates)
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
    rule: Callable[[list[float]], Verdict] | None = None,
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
    The split gate uses the production deltas and the SE floor from the repeat spread inside each case.
    `rule` replaces the split gate's verdict function with one that sees only the deltas; tests inject a weakened gate with it.
    Raise `ValueError` when `trials < 1`, `cases < 2`, `repeats < 1`, or `candidates < 2`.
    """
    _check_shape("simulate", trials, cases, repeats)
    if candidates < 2:
        raise ValueError("simulate needs at least 2 candidates")
    rng = random.Random(seed)
    split = single = 0
    for _ in range(trials):
        won_split, won_single = _trial(rng, cases, repeats, candidates, rule)
        split += won_split
        single += won_single
    return Simulation(trials, cases, split / trials, single / trials, split, single)


def simulate_power(
    effect: float,
    seed: int = 20261006,
    trials: int = 2000,
    cases: int = 10,
    repeats: int = 3,
    *,
    rule: Callable[[list[float]], Verdict] | None = None,
) -> Power:
    """Estimate how often the gate promotes a candidate that truly beats the baseline by about `effect`.

    The candidate chance on each case is `clip(gauss(base + effect, 0.10))`, so `effect` is the mean shift before the
    per-case spread and the clip to [0, 1]. The clip removes part of the shift near the ceiling, so the realised mean
    gain is lower than `effect`: about 0.097, 0.188, and 0.271 for 0.1, 0.2, and 0.3.
    The run uses the seed, case count, repeats, baseline cases, and gate of `simulate`.
    It skips the validation search, so each trial holds one baseline and one candidate on `cases` holdout cases.
    `rule` replaces the verdict function, as in `simulate`; a rule that never promotes gets zero power.
    Raise `ValueError` when `trials < 1`, `cases < 2`, `repeats < 1`, or `effect` is not above 0.
    """
    _check_shape("simulate_power", trials, cases, repeats)
    if not effect > 0:
        raise ValueError("simulate_power needs an effect above 0")
    rng = random.Random(seed)
    promotions = 0
    for _ in range(trials):
        base = [rng.betavariate(2, 2) for _ in range(cases)]
        candidate = _chances(rng, [chance + effect for chance in base], 0.10)
        base_draws = _draws(rng, base, repeats)
        win_draws = _draws(rng, candidate, repeats)
        if rule is None:
            result = _floored(base_draws, win_draws)
        else:
            result = rule([sum(w) / repeats - sum(b) / repeats for w, b in zip(win_draws, base_draws)])
        promotions += result.verdict == "promote"
    return Power(effect, trials, cases, promotions, promotions / trials)


def _check_shape(name: str, trials: int, cases: int, repeats: int) -> None:
    if trials < 1:
        raise ValueError(f"{name} needs at least 1 trial")
    if cases < 2:
        raise ValueError(f"{name} needs at least 2 cases")
    if repeats < 1:
        raise ValueError(f"{name} needs at least 1 repeat")


CHEAPER_LOSS = 0.05
CHEAPER_TOKEN_RATIO = 0.5
CHEAPER_MIN_SAVING = 0.3
CHEAPER_SCENARIOS = ((0.05, 0.0, 0), (0.05, 0.02, 0), (0.05, 0.05, 0), (0.0, 0.05, 0), (0.0, 0.05, 1))
"""The (true loss, `max_quality_loss` margin, fixed cases) triples that `self-test --simulate` reports."""


@dataclass(frozen=True)
class Cheaper:
    """The `promote-cheaper` rate of the production gate when the winner truly loses `loss` per case.

    The winner costs `CHEAPER_TOKEN_RATIO` of the baseline task tokens on every case.
    `margin` is `max_quality_loss`, and `min_token_saving` is `CHEAPER_MIN_SAVING`. `rate` equals `promotions / trials`.
    """

    loss: float
    margin: float
    trials: int
    cases: int
    promotions: int
    rate: float
    fixed: int = 0


def simulate_cheaper(
    loss: float = CHEAPER_LOSS,
    margin: float = 0.0,
    seed: int = 20261006,
    trials: int = 2000,
    cases: int = 10,
    repeats: int = 3,
    candidates: int = 5,
    fixed: int = 0,
) -> Cheaper:
    """Estimate how often the gate returns `promote-cheaper` for a winner that truly loses `loss` per case.

    `fixed` is the number of leading holdout cases that the winner solves outright: the baseline chance is 0.5 and the
    winner chance is 1.0 there.

    Each trial selects the best of `candidates` on a validation set, as `simulate` does, but every candidate chance
    sits `loss` below the baseline chance before the per-case spread. The holdout uses the production deltas, the
    SE floor, and the token rule. Raise `ValueError` for the shape errors of `simulate`, or a `loss` or `margin` below 0.
    """
    _check_shape("simulate_cheaper", trials, cases, repeats)
    if candidates < 2:
        raise ValueError("simulate_cheaper needs at least 2 candidates")
    if not (loss >= 0 and margin >= 0):
        raise ValueError("simulate_cheaper needs a loss and a margin of at least 0")
    if not 0 <= fixed <= cases:
        raise ValueError("simulate_cheaper needs fixed cases between 0 and the case count")
    rng = random.Random(seed)
    thresholds = Statistics(min_token_saving=CHEAPER_MIN_SAVING, max_quality_loss=margin)
    promotions = 0
    for _ in range(trials):
        base, chosen = _selected(rng, cases, repeats, candidates, loss)
        base[:fixed], chosen[:fixed] = [0.5] * fixed, [1.0] * fixed
        paired = holdout_deltas({f"{index:06d}": case for index, case in enumerate(_draws(rng, base, repeats))},
                                {f"{index:06d}": case for index, case in enumerate(_draws(rng, chosen, repeats))})
        tokens = {case_id: (100.0, 100.0 * CHEAPER_TOKEN_RATIO) for case_id in paired.deltas}
        result = verdict(paired.deltas, thresholds=thresholds, tokens=tokens, se_floor=paired.se_floor, planned=cases)
        promotions += result.verdict == "promote-cheaper"
    return Cheaper(loss, margin, trials, cases, promotions, promotions / trials, fixed)
