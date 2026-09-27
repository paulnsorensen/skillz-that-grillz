"""Run one operation across many items at once.

Every skill operation (build, lock, publish) spends its time in ``uv``, shiv,
and ``gh`` subprocesses, so threads give the parallelism and one item's
failure never stops the others. Callers get one outcome per item, in the
order the items were given, and decide how to report the failures.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from wedge._config import ConfigError
from wedge._guard import GuardError

I = TypeVar("I")
T = TypeVar("T")

# Errors an operation raises on purpose; their message stands alone.
_EXPECTED_ERRORS = (ConfigError, GuardError, ValueError, OSError)


@dataclass(frozen=True)
class Outcome(Generic[I, T]):
    """What one item's operation produced: a value, or the reason it failed."""

    item: I
    value: T | None = None
    error: str | None = None


def default_jobs(count: int) -> int:
    """Workers for ``count`` items: one per CPU, never more than the items."""
    return max(1, min(count, os.cpu_count() or 1))


def describe_error(exc: BaseException) -> str:
    """One line a person can act on, with a subprocess's stderr when it has one."""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = str(cast(object, exc.stderr) or "").strip()
        detail = f": {stderr}" if stderr else ""
        program = cast(Sequence[str], exc.cmd)[0]
        return f"{program} exited {exc.returncode}{detail}"
    if isinstance(exc, _EXPECTED_ERRORS):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def fan_out(
    items: Sequence[I],
    operation: Callable[[I], T],
    *,
    jobs: int | None = None,
) -> list[Outcome[I, T]]:
    """Apply ``operation`` to every item on up to ``jobs`` threads.

    Returns one outcome per item in input order. An exception becomes the
    outcome's ``error`` and the other items keep running.
    """
    if not items:
        return []
    workers = jobs if jobs is not None and jobs > 0 else default_jobs(len(items))

    def run(item: I) -> Outcome[I, T]:
        try:
            return Outcome(item, value=operation(item))
        except Exception as exc:  # noqa: BLE001 - one item's failure is data, not a crash
            return Outcome(item, error=describe_error(exc))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, items))
