from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import cast


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


def _coverage(value: object) -> float | None:
    """Return `value` as a float when it lies from 0 to 1; else return None."""
    number = threshold(value)
    return number if number is not None and number <= 1 else None


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

    Every field except `min_token_saving` and `max_quality_loss` only tightens `promote`. Those two fields set only
    `promote-cheaper`, and a larger `max_quality_loss` loosens it. That verdict claims no gain. It needs the delta
    to be at least 0 and `delta - 2*se_loss` to be at least `-max_quality_loss`, so `max_quality_loss` bounds the
    plausible quality loss. `se_loss` is the SE of the case losses, `min(d_i, 0)`, with the SE floor.
    While `max_quality_loss` is None, `promote-cheaper` is off.
    The SE multiplier stays 2. `min_gain` and `min_lower_bound` are floors of at least 0.
    A field with `flag` metadata also has a CLI flag. A field with `nullable` metadata may be None, which
    turns its rule off. A field with `map` metadata holds a name-to-number map and is contract-only.
    `max_token_increase_per_gain` and `min_token_saving` set the token rule, which is off while both are None.
    `min_token_saving` is a saving share strictly between 0 and 1.
    `min_coverage` is the least share of planned holdout cases that must be scored, from 0 to 1. Below it the
    verdict is `inconclusive`. 0 turns the rule off.
    A field with `bounds` metadata holds the JSON Schema bounds of its value. The default bound is a minimum of 0.
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
        "flag": True, "nullable": True, "check": _saving, "rule": "null or a number above 0 and below 1",
        "bounds": {"exclusiveMinimum": 0, "exclusiveMaximum": 1}})
    min_coverage: float = field(default=0.6, metadata={
        "flag": True, "check": _coverage, "rule": "a number from 0 to 1", "bounds": {"minimum": 0, "maximum": 1}})
    max_quality_loss: float | None = field(default=None, metadata={"flag": True, "nullable": True})

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


@dataclass(frozen=True)
class HoldoutDeltas:
    """The paired holdout result: per-case deltas, the SE floor, the per-case moves, and the shared case count."""

    deltas: dict[str, float]
    se_floor: float
    moves: dict[str, list[str]]
    n_shared: int


def _repeat_se(scores: Sequence[float]) -> float:
    """Return the standard error of the repeat mean. One repeat gives 0."""
    return statistics.stdev(scores) / math.sqrt(len(scores)) if len(scores) > 1 else 0.0


def holdout_deltas(
    baseline: Mapping[str, Sequence[float]], winner: Mapping[str, Sequence[float]]
) -> HoldoutDeltas:
    """Pair the scored repeats of both arms per case and return the deltas, the SE floor, and the moves.

    A case with no scored repeat on an arm drops out of the deltas, the moves, and the floor.
    The floor is `sqrt(sum(se_b^2 + se_w^2)) / n` over the shared cases, where each `se` is the standard error of
    the repeat mean of one case on one arm. A move is resolved when the mean delta exceeds
    `max(2 * sqrt(se_b^2 + se_w^2), 1e-9)`; a resolved positive move is `fixed`, a resolved negative move is
    `broke`, and the rest are `unresolved`. Each list of case ids is sorted.
    """
    shared = sorted(case_id for case_id in baseline.keys() & winner.keys() if baseline[case_id] and winner[case_id])
    deltas: dict[str, float] = {}
    moves: dict[str, list[str]] = {"fixed": [], "broke": [], "unresolved": []}
    total = 0.0
    for case_id in shared:
        base, won = baseline[case_id], winner[case_id]
        delta = statistics.fmean(won) - statistics.fmean(base)
        variance = _repeat_se(base) ** 2 + _repeat_se(won) ** 2
        deltas[case_id] = delta
        total += variance
        if abs(delta) > max(2 * math.sqrt(variance), 1e-9):
            moves["fixed" if delta > 0 else "broke"].append(case_id)
        else:
            moves["unresolved"].append(case_id)
    floor = math.sqrt(total) / len(shared) if shared else 0.0
    return HoldoutDeltas(deltas, floor, moves, len(shared))
