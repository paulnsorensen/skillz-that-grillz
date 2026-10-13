from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from skillz_experiments.gate._statistics import DEFAULT_STATISTICS, Statistics

VerdictName = Literal["promote", "promote-cheaper", "inconclusive", "reject"]

def _no_moves() -> dict[str, list[str]]:
    return {"fixed": [], "broke": [], "unresolved": []}


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
    se_floor: float = 0.0
    moves: dict[str, list[str]] = field(default_factory=_no_moves, hash=False)
    n_shared: int = 0
    """The number of cases scored on both arms. It is 0 when the gate scores no case."""

    def data(self) -> dict[str, object]:
        """Return the gate keys of the run record, with each tuple as a list."""
        return {"verdict": self.verdict, "delta": self.delta, "se": self.se, "cases": self.cases,
                "reasons": list(self.reasons), "skipped_families": list(self.skipped_families),
                "token_delta": self.token_delta, "token_se": self.token_se,
                "se_floor": self.se_floor, "n_shared": self.n_shared,
                "moves": {name: list(ids) for name, ids in self.moves.items()}}


def _family_rule(deltas: Mapping[str, float], families: Mapping[str, str] | None,
                 thresholds: Statistics) -> tuple[list[str], tuple[str, ...]]:
    """Return the breached families and the skipped families, each sorted by family name.

    The rule skips a family with fewer than 2 holdout cases, whatever its budget. A family with no budget is off.
    A family that `family_budgets` names but that has no holdout case counts as skipped too.
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
    skipped.extend(name for name in thresholds.family_budgets if name not in grouped)
    return breached, tuple(sorted(skipped))


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
            tokens: Mapping[str, tuple[float | None, float | None]] | None = None,
            se_floor: float = 0.0, planned: int | None = None) -> Verdict:
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
    `min_token_saving` is set, the delta is at least 0, `delta - 2*se` is at least `-max_quality_loss`, the tokens
    drop by more than twice the token SE, and the drop is at least `min_token_saving`. A None `max_quality_loss`
    turns `promote-cheaper` off. The floors do not gate `promote-cheaper`.
    Unknown usage turns a `promote` into `inconclusive` with reason `unknown-usage`. It adds the same reason to
    a band result when `min_token_saving` is set. An active token rule with no `tokens` is unknown usage.
    `token_delta` and `token_se` report the change while the rule runs and the usage is known.
    The SE is `max(raw SE, se_floor)`: the floor never lowers it. The zero-SE branch applies only when that is 0.
    Fewer than 2 cases give an SE of `se_floor`.
    `planned` is the number of planned cases. The coverage rule runs when `planned` is set and `min_coverage` is
    above 0: if `len(deltas) / planned` is below `min_coverage`, the verdict is `inconclusive` with reason
    `low-coverage`. It runs before the floors and the token rule, keeps `delta` and `se`, and the family rule
    can still force `reject`.
    """
    if families is not None and not isinstance(deltas, Mapping):
        raise ValueError("families need deltas keyed by case id")
    if tokens is not None and (not isinstance(deltas, Mapping) or tokens.keys() != deltas.keys()):
        raise ValueError("tokens need deltas keyed by the same case ids")
    values = list(deltas.values()) if isinstance(deltas, Mapping) else list(deltas)
    count = len(values)
    low = (planned is not None and thresholds.min_coverage > 0
           and (planned <= 0 or count / planned < thresholds.min_coverage))
    cover = ["low-coverage"] if low else []
    breached, skipped = _family_rule(deltas if isinstance(deltas, Mapping) else {}, families, thresholds)
    limit, saving = thresholds.max_token_increase_per_gain, thresholds.min_token_saving
    active = thresholds.token_rule
    change = _token_change(tokens) if active and tokens is not None else None
    token_delta, token_se = change if change is not None else (None, None)
    if not all(math.isfinite(value) for value in values):
        return Verdict("inconclusive", math.nan, math.nan, count, tuple(cover), skipped_families=skipped,
                       token_delta=token_delta, token_se=token_se, se_floor=se_floor, n_shared=count)
    if count < 2:
        return Verdict("inconclusive", statistics.fmean(values) if values else 0.0, se_floor, count,
                       tuple(cover), skipped_families=skipped, token_delta=token_delta, token_se=token_se,
                       se_floor=se_floor, n_shared=count)
    delta = statistics.fmean(values)
    se = max(statistics.stdev(values) / math.sqrt(count), se_floor)
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
    reasons: list[str] = list(cover)
    if low:
        name = "inconclusive"
    elif name == "promote":
        if not delta >= thresholds.min_gain:
            reasons.append("min-gain")
        if not delta > 2 * se + thresholds.min_lower_bound:
            reasons.append("min-lower-bound")
        if reasons:
            name = "inconclusive"
    if active and not low:
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
            elif (thresholds.max_quality_loss is not None and delta >= 0
                  and _reaches(delta - 2 * se, -thresholds.max_quality_loss)
                  and _exceeds(-token_delta, 2 * token_se) and _reaches(-token_delta, saving)):
                name = "promote-cheaper"
    if breached:
        name = "reject"
        reasons.extend(f"family-regression:{family}" for family in breached)
    return Verdict(name, delta, se, count, tuple(reasons), skipped, token_delta, token_se, se_floor, n_shared=count)
