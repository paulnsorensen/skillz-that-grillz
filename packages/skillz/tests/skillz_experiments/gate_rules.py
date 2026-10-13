from __future__ import annotations

from skillz_experiments.gate import Verdict


def always_promote(deltas: list[float]) -> Verdict:
    return Verdict("promote", 0.0, 0.0, len(deltas))


def never_promote(deltas: list[float]) -> Verdict:
    return Verdict("reject", 0.0, 0.0, len(deltas))
