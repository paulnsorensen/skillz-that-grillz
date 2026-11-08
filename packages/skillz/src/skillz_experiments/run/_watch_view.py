"""The view of a run record that `watch` serves: status, roles, leaderboard, cases, reflections, and outcomes.

The view keeps no transcript, no candidate file, and no case text. A field that a run has not written yet is absent or null.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from math import isfinite
from typing import cast

# The newest outcomes that the view lists one by one. The cases and the leaderboard count every outcome.
OUTCOME_ROWS = 200
SHORT = 12
USAGE_FIELDS = {"task": ("task_usage", "latency_seconds"), "judge": ("judge_usage", "judge_latency_seconds"),
                "simulator": ("simulator_usage", None)}

Record = Mapping[str, object]


def _mapping(value: object) -> Record:
    return cast(Record, value) if isinstance(value, dict) else {}


def _items(value: object) -> list[Record]:
    """Return the objects of a recorded list. A missing or malformed list is empty."""
    if not isinstance(value, list):
        return []
    return [cast(Record, item) for item in cast(list[object], value) if isinstance(item, dict)]


def _number(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(cast(float, value))
    except OverflowError as error:
        raise ValueError("invalid run number") from error
    if not isfinite(number):
        raise ValueError("invalid run number")
    return number


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _short(value: object) -> str | None:
    text = _text(value)
    return None if text is None else text[:SHORT]


def _mean(values: Iterable[float | None]) -> float | None:
    found = [value for value in values if value is not None]
    return sum(found) / len(found) if found else None


def _criteria(outcome: Record) -> list[dict[str, object]]:
    """Return the judged criteria of one outcome in record order: id, level, met, and the judge reason."""
    found: list[dict[str, object]] = []
    for name, verdict in _mapping(outcome.get("criteria")).items():
        if isinstance(verdict, dict):
            item = cast(Record, verdict)
            found.append({"id": name, "level": _number(item.get("level")), "met": item.get("met") is True,
                          "reason": _text(item.get("reason"))})
    return found


def _status(record: Record) -> dict[str, object]:
    budget, search, gate = (_mapping(record.get(key)) for key in ("budget", "search", "gate"))
    seconds = [_number(value) for value in _mapping(record.get("seconds_by_role")).values()]
    comparisons = [{"challenger": _short(item.get("challenger")), "incumbent": _short(item.get("incumbent")),
                    "verdict": _text(item.get("verdict")), "delta": _number(item.get("delta")), "se": _number(item.get("se"))}
                   for item in _items(search.get("comparisons"))]
    return {"run_id": _text(record.get("run_id")), "phase": _text(record.get("phase")), "model": _text(record.get("model")),
            "calls": _number(record.get("calls")), "call_cap": _number(budget.get("maximum")),
            "seconds": sum(value for value in seconds if value is not None), "seconds_cap": _number(budget.get("seconds")),
            "stop_trigger": _text(search.get("stop_trigger")), "search_reason": _text(search.get("reason")),
            "gate_verdict": _text(gate.get("verdict")), "gate_delta": _number(gate.get("delta")),
            "gate_se": _number(gate.get("se")), "comparisons": comparisons}


def _roles(record: Record, judged: list[Record], reflections: list[Record]) -> list[dict[str, object]]:
    """Return one row per role: recorded calls and seconds, and the tokens and latency of its outcomes."""
    usage: defaultdict[str, list[tuple[Record, float | None]]] = defaultdict(list)
    for outcome in judged:
        if outcome.get("status") == "candidate-contract-rejected":
            continue
        for role, (field, latency) in USAGE_FIELDS.items():
            key = ("task_usage" if "task_usage" in outcome else "usage") if role == "task" else field
            if isinstance(outcome.get(key), dict):
                usage[role].append((_mapping(outcome.get(key)), _number(outcome.get(latency)) if latency else None))
    for outcome in reflections:
        if isinstance(outcome.get("usage"), dict):
            usage["reflection"].append((_mapping(outcome.get("usage")), _number(outcome.get("latency_seconds"))))
    calls, seconds = _mapping(record.get("calls_by_role")), _mapping(record.get("seconds_by_role"))
    rows: list[dict[str, object]] = []
    for role in sorted(set(usage) | set(calls) | set(seconds), key=lambda name: (-(_number(calls.get(name)) or 0), name)):
        items = usage.get(role, [])
        tokens: dict[str, float | None] = {}
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            values = [value for item, _ in items if (value := _number(item.get(key))) is not None]
            tokens[key] = sum(values) if values else None
        rows.append({"role": role, "calls": _number(calls.get(role)), "seconds": _number(seconds.get(role)),
                     "outcomes": len(items), **tokens, "mean_latency": _mean(latency for _, latency in items)})
    return rows


def _leaderboard(judged: list[Record]) -> list[dict[str, object]]:
    """Return one row per split, arm, and candidate, best mean score first in each split."""
    groups: defaultdict[tuple[str, str, str], list[Record]] = defaultdict(list)
    for outcome in judged:
        candidate = _short(outcome.get("search_candidate")) or _short(outcome.get("candidate_hash")) or "?"
        groups[(_text(outcome.get("split")) or "?", _text(outcome.get("arm")) or "?", candidate)].append(outcome)
    rows = [_scores({"split": split, "arm": arm, "candidate": candidate}, items)
            for (split, arm, candidate), items in groups.items()]
    return sorted(rows, key=lambda row: (str(row["split"]), row["mean"] is None, -(cast(float | None, row["mean"]) or 0.0),
                                         str(row["candidate"])))


def _scores(row: dict[str, object], items: list[Record]) -> dict[str, object]:
    scores = [_number(item.get("score")) if item.get("status") != "evaluation-failed" else None for item in items]
    return row | {"cases": len({_text(item.get("case_id")) or "?" for item in items}), "runs": len(items),
                  "scored": sum(score is not None for score in scores), "mean": _mean(scores),
                  "zero_runs": sum(score == 0 for score in scores)}


def _cases(judged: list[Record]) -> list[dict[str, object]]:
    """Return one row per case: its scores, fault statuses, and each criterion with its lowest level and judge reason."""
    groups: defaultdict[str, list[Record]] = defaultdict(list)
    for outcome in judged:
        groups[_text(outcome.get("case_id")) or "?"].append(outcome)
    rows: list[dict[str, object]] = []
    for case_id, items in groups.items():
        splits = sorted({_text(item.get("split")) or "?" for item in items})
        statuses = sorted({status for item in items if (status := _text(item.get("status"))) is not None})
        rows.append(_scores({"case_id": case_id, "splits": splits, "statuses": statuses}, items)
                    | {"criteria": _case_criteria(items)})
    return sorted(rows, key=lambda row: (-cast(int, row["zero_runs"]), cast(float | None, row["mean"]) or 0.0,
                                         str(row["case_id"])))


def _case_criteria(items: list[Record]) -> list[dict[str, object]]:
    verdicts: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for item in items:
        for verdict in _criteria(item):
            verdicts[cast(str, verdict["id"])].append(verdict)
    rows: list[dict[str, object]] = []
    for name, found in verdicts.items():
        unmet = [verdict for verdict in found if not verdict["met"]]
        worst = min(unmet or found, key=lambda verdict: cast(float | None, verdict["level"]) or 0.0)
        rows.append({"id": name, "runs": len(found), "unmet": len(unmet), "lowest_level": worst["level"],
                     "reason": worst["reason"]})
    return sorted(rows, key=lambda row: (-cast(int, row["unmet"]), cast(float | None, row["lowest_level"]) or 0.0,
                                         str(row["id"])))


def _reflection(step: int, outcome: Record) -> dict[str, object]:
    changed = outcome.get("changed")
    return {"step": step, "hypothesis": _text(outcome.get("hypothesis")), "notes": _text(outcome.get("notes")),
            "parent": _short(outcome.get("parent")), "proposed": _short(outcome.get("proposed")),
            "changed": len(cast(list[object], changed)) if isinstance(changed, list) else None,
            "over_budget": outcome.get("over_budget") is True, "rejected": _text(outcome.get("rejected")),
            "latency": _number(outcome.get("latency_seconds")),
            "output_tokens": _number(_mapping(outcome.get("usage")).get("output_tokens"))}


def _outcome(index: int, outcome: Record) -> dict[str, object]:
    return {"index": index, "case_id": _text(outcome.get("case_id")), "split": _text(outcome.get("split")),
            "arm": _text(outcome.get("arm")), "candidate": _short(outcome.get("search_candidate")) or _short(outcome.get("candidate_hash")),
            "repeat": _number(outcome.get("repeat")),
            "score": None if outcome.get("status") == "evaluation-failed" else _number(outcome.get("score")),
            "status": _text(outcome.get("status")), "latency": _number(outcome.get("latency_seconds")),
            "task_tokens": _number(_mapping(outcome.get("task_usage" if "task_usage" in outcome else "usage")).get("input_tokens"))
            if outcome.get("status") != "candidate-contract-rejected" else None,
            "criteria": _criteria(outcome)}


def view(record: Record) -> dict[str, object]:
    """Return the watch view of `record`. Malformed parts are left out, so a record of any phase gives a view."""
    outcomes = _items(record.get("outcomes"))
    judged = [item for item in outcomes if item.get("split") != "reflection"]
    reflections = [item for item in outcomes if item.get("split") == "reflection"]
    newest = list(enumerate(judged, start=1))[-OUTCOME_ROWS:]
    return {"status": _status(record), "roles": _roles(record, judged, reflections), "leaderboard": _leaderboard(judged),
            "cases": _cases(judged),
            "reflections": [_reflection(step, item) for step, item in enumerate(reflections, start=1)],
            "outcomes": [_outcome(index, item) for index, item in reversed(newest)], "outcome_count": len(judged)}
