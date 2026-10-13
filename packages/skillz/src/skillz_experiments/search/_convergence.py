"""The stall signal: how many proposals in a row gave no validation gain.

`run/` rebuilds the observations from the run record, so a resumed run gets the same signal.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Level = Literal["ok", "warn", "shift", "stop"]

WARN_AT = 3
SHIFT_AT = 5
STOP_AT = 8
# Below this many known observations the signal is always `ok`.
MIN_KNOWN = 4


@dataclass(frozen=True)
class Signal:
    """The stall level, the streak of known observations, and the prompt advice. The advice is empty at ok and stop."""
    level: Level
    streak: int
    advice: str


def assess(observations: Sequence[float | None], *, band: float) -> Signal:
    """Return the stall signal for the proposals so far, oldest first.

    An observation is the rise of the best validation mean after one proposal, or None when it is unknown.
    A rise above `band` is a gain and resets the streak. Any other rise adds 1. None changes nothing.
    """
    known = [rise for rise in observations if rise is not None]
    streak = 0
    for rise in known:
        streak = 0 if rise > band else streak + 1
    if len(known) < MIN_KNOWN or streak < WARN_AT:
        return Signal("ok", streak, "")
    if streak >= STOP_AT:
        return Signal("stop", streak, "")
    head = f"The last {streak} proposals gave no validation gain. "
    if streak >= SHIFT_AT:
        return Signal("shift", streak, head + "Change a different component or section. Do not refine the same lines again.")
    return Signal("warn", streak, head + "Try a different kind of change.")
