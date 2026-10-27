"""Measure how the CPU cost of a call grows with its input on a host whose CPU speed changes under load.

CPU time is not a fixed unit of work. Two core types (such as 5.1 GHz and 3.3 GHz cores), a busy SMT sibling, or a
lower clock under load can make the same call cost three times more CPU time than a moment earlier. So a linearity
check uses a 16x size step: a linear cost grows by 16 and a quadratic cost by 256, and the limit of 64 between them
stays a factor of 4 from each.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

Input = TypeVar("Input")

STEP = 16
LIMIT = 64


def best_seconds(call: Callable[[Input], object], small: Input, large: Input, *, rounds: int = 7) -> tuple[float, float]:
    """Return the best CPU time of `call(small)` and of `call(large)`.

    The two sizes alternate, one call each per round, and the order flips each round. A speed change thus hits both
    sizes alike, and the best time of each size comes from a fast period. The clock counts this thread only, so the
    other threads of the process, such as the xdist channel thread, do not add to it.
    """
    best = [float("inf"), float("inf")]
    for index in range(rounds):
        for size in (0, 1) if index % 2 == 0 else (1, 0):
            begin = time.thread_time()
            _ = call(large if size else small)
            best[size] = min(best[size], time.thread_time() - begin)
    return best[0], best[1]
