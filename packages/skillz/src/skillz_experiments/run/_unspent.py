"""The unspent holdout reserve of a finished run: the `search_end` snapshot and its validated reading (gh-588)."""
from __future__ import annotations

import time
from typing import cast

from skillz_experiments._budget import Budget
from skillz_experiments._cases import CodedError


def search_end(budget: Budget, record: dict[str, object]) -> dict[str, float | int]:
    """Return the calls spent, the seconds left, and the preflight calls as the search ends. `with_unspent` reads it."""
    left = max(0.0, budget.seconds - (time.monotonic() - budget.started))
    return {"calls": budget.calls, "seconds_left": round(left, 3), "preflight_calls": _count(record.get("preflight_calls")) or 0}


def _number(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _search_end(record: dict[str, object], budget: Budget, total_seconds: float) -> tuple[int, float, int] | None:
    """Return the validated `search_end` snapshot as (calls, seconds left, preflight calls).

    Return None when the record has no snapshot (a run recorded before the snapshot existed). Raise when it is not sound.
    """
    if "search_end" not in record:
        return None
    raw = record["search_end"]
    snapshot = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    calls, preflight = _count(snapshot.get("calls")), _count(snapshot.get("preflight_calls", 0))
    left = _number(snapshot.get("seconds_left"), -1.0)
    if calls is None or preflight is None or calls > budget.calls or not 0.0 <= left <= total_seconds:
        raise CodedError("run-record-tampered", "the recorded search end is out of range; create a new run")
    return calls, left, preflight


def with_unspent(record: dict[str, object], budget: Budget) -> dict[str, object]:
    """Return the recorded budget block with `unspent`: the reserve calls and seconds that the run did not spend.

    The search never spends the reserve. The gate and the replay do. The record's `search_end` entry is the
    `search_end` snapshot, so a search that under-spends does not count as unspent reserve: only the calls and
    seconds since the search ended count against the reserve. A resume re-runs the preflight, and its calls do not
    count. A record with no snapshot is an old run: it gets no `unspent`. Raise `run-record-tampered` when the
    snapshot is present but out of range.
    """
    block = record.get("budget")
    recorded = cast("dict[str, object]", block) if isinstance(block, dict) else {}
    held = _number(recorded.get("reserve_seconds"), 0.0)
    ended = _search_end(record, budget, _number(recorded.get("seconds"), budget.seconds))
    if ended is None:
        return dict(recorded)
    calls, ended_left, preflight = ended
    left = max(0.0, budget.seconds - (time.monotonic() - budget.started))
    grown = max(0, (_count(record.get("preflight_calls")) or 0) - preflight)
    used_calls = max(0, budget.calls - calls - grown)
    used_seconds = max(0.0, ended_left - left)
    unspent_calls = max(0, min(budget.reserve - used_calls, budget.maximum - budget.calls))
    return {**recorded, "unspent": {"calls": unspent_calls, "seconds": round(max(0.0, min(held - used_seconds, left)), 3)}}
