"""Per-proposal attempts, rebuilt from the recorded outcomes, and the history text of the reflection prompt.

An attempt holds identities, digests, and case ids. It never holds request text, expected text, or component text.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

SHOWN = 8
LIMIT = 4000
MAX_IDS = 10
MAX_PROTECTED = 30
MAX_REASON = 80

INTRO = ("Attempt history, newest first. "
         "Each attempt lists its hypothesis, the train cases it fixed, and the train cases it broke. ")
RULES = ("Do not re-introduce a change that broke a case. "
         "Do not copy a case id into any component or into @hypothesis. "
         "The case-id check rejects a component that names one and drops such a hypothesis.")

Scores = dict[str, dict[str, float]]


def _is_reflection(item: Mapping[str, object]) -> bool:
    return item.get("arm") == "search" and item.get("split") == "reflection"


def _newest(outcomes: Sequence[Mapping[str, object]], split: str) -> Scores:
    """Return the newest score of each (candidate, case) on `split`. An evaluation-failed outcome is unscored."""
    scores: Scores = {}
    for item in outcomes:
        candidate, case, score = item.get("search_candidate"), item.get("case_id"), item.get("score")
        if item.get("arm") != "search" or item.get("split") != split or item.get("status") == "evaluation-failed":
            continue
        if isinstance(candidate, str) and isinstance(case, str) and isinstance(score, (int, float)) and not isinstance(score, bool):
            scores.setdefault(candidate, {})[case] = float(score)
    return scores


def _ids(value: object) -> list[str]:
    return [name for name in cast(list[object], value) if isinstance(name, str)] if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _mean(scores: Mapping[str, float] | None) -> float | None:
    return sum(scores.values()) / len(scores) if scores else None


def attempts(outcomes: Sequence[Mapping[str, object]], seed: tuple[str, str]) -> list[dict[str, object]]:
    """Return one attempt per recorded reflection, oldest first. `seed` is (digest of the seed component texts, seed identity).

    The parent digest of a reflection resolves to a candidate identity through the seed and the earlier proposals.
    An unresolved parent, an unscored child, or a legacy outcome gives empty `fixed` and `broke` lists and a null `validation_delta`.
    """
    reflections = [item for item in outcomes if _is_reflection(item)]
    identities = {seed[0]: seed[1]}
    for item in reflections:
        key, proposed = _text(item.get("components_digest")), _text(item.get("proposed"))
        if key is not None and proposed is not None:
            _ = identities.setdefault(key, proposed)
    train, validation = _newest(outcomes, "train"), _newest(outcomes, "validation")
    found: list[dict[str, object]] = []
    for index, item in enumerate(reflections, 1):
        key = _text(item.get("parent"))
        parent = identities.get(key) if key is not None else None
        child = _text(item.get("proposed"))
        fixed: list[str] = []
        broke: list[str] = []
        delta: float | None = None
        if parent is not None and child is not None:
            for case in _ids(item.get("minibatch")):
                before, after = train.get(parent, {}).get(case), train.get(child, {}).get(case)
                if before is None or after is None:
                    continue
                if after > before:
                    fixed.append(case)
                elif after < before:
                    broke.append(case)
            was, now = _mean(validation.get(parent)), _mean(validation.get(child))
            delta = None if was is None or now is None else now - was
        found.append({"index": index, "parent": parent, "candidate": child, "components": _text(item.get("components_digest")),
                      "hypothesis": _text(item.get("hypothesis")), "fixed": fixed, "broke": broke,
                      "validation_delta": delta, "accepted": child is not None, "rejected": _text(item.get("rejected"))})
    return found


def protected(outcomes: Sequence[Mapping[str, object]], identity: str) -> list[str]:
    """Return the train case ids on which candidate `identity` has a full score, sorted."""
    return sorted(case for case, score in _newest(outcomes, "train").get(identity, {}).items() if score >= 1.0)


def _list(names: Sequence[str]) -> str:
    if not names:
        return "none"
    return ", ".join(names[:MAX_IDS]) + (f" and {len(names) - MAX_IDS} more" if len(names) > MAX_IDS else "")


def _line(attempt: Mapping[str, object]) -> str:
    hypothesis = _text(attempt.get("hypothesis"))
    delta = attempt.get("validation_delta")
    rejected = _text(attempt.get("rejected"))
    result = "admitted" if rejected is None else "rejected, " + " ".join(rejected.split())[:MAX_REASON]
    change = f"{delta:+.3f}" if isinstance(delta, (int, float)) else "unknown"
    said = " ".join(hypothesis.split()) if hypothesis else "none"
    return (f"Attempt {attempt.get('index')}: hypothesis {said}; "
            f"fixed {_list(_ids(attempt.get('fixed')))}; broke {_list(_ids(attempt.get('broke')))}; "
            f"validation change {change}; result {result}.")


def render(history: Sequence[Mapping[str, object]], protect: Sequence[str] = ()) -> str:
    """Return the one-line history section: the newest `SHOWN` attempts newest first, the protect set, and the rules.

    The section stays within `LIMIT` characters. The oldest attempt drops first, then the protect ids.
    """
    lines = [_line(attempt) for attempt in reversed(history[-SHOWN:])]
    keep = list(protect[:MAX_PROTECTED])

    def compose() -> str:
        parts = [INTRO + (" ".join(lines) if lines else "No attempt yet.")]
        if keep:
            parts.append("Keep these train cases passing, because the current best passes them: " + ", ".join(keep) + ".")
        return " ".join([*parts, RULES])

    while len(compose()) > LIMIT and lines:
        _ = lines.pop()
    while len(compose()) > LIMIT and keep:
        _ = keep.pop()
    return compose()
