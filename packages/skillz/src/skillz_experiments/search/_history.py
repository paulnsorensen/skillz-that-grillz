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


def is_reflection(item: Mapping[str, object]) -> bool:
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


def _validation(outcomes: Sequence[Mapping[str, object]], cases: Sequence[str]) -> Scores:
    """Return the newest validation score of each (candidate, case in `cases`), as the session counts it.

    An evaluation-failed outcome counts as 0.0, and every score is clamped to 0..1.
    """
    wanted = set(cases)
    scores: Scores = {}
    for item in outcomes:
        candidate, case, score = item.get("search_candidate"), item.get("case_id"), item.get("score")
        if item.get("arm") != "search" or item.get("split") != "validation" or case not in wanted:
            continue
        if not isinstance(candidate, str) or not isinstance(case, str):
            continue
        if item.get("status") == "evaluation-failed":
            value = 0.0
        elif isinstance(score, (int, float)) and not isinstance(score, bool):
            value = min(1.0, max(0.0, float(score))) if score == score else 0.0
        else:
            continue
        scores.setdefault(candidate, {})[case] = value
    return scores


def _ids(value: object) -> list[str]:
    return [name for name in cast(list[object], value) if isinstance(name, str)] if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _mean(scores: Mapping[str, float] | None, cases: Sequence[str]) -> float | None:
    """Return the mean of `scores` over `cases`, or None unless it holds every one of them."""
    if not cases or scores is None or set(scores) != set(cases):
        return None
    return sum(scores.values()) / len(scores)


def attempts(outcomes: Sequence[Mapping[str, object]], seed: tuple[str, str],
             validation: Sequence[str] = ()) -> list[dict[str, object]]:
    """Return one attempt per recorded reflection, oldest first. `seed` is (digest of the seed component texts, seed identity).

    `validation` holds the validation case ids. The validation change needs a newest score on every one of them for the child and the parent.
    An evaluation-failed validation outcome counts as 0.0, as in the session.

    The parent digest of a reflection resolves to a candidate identity through the seed and the earlier proposals.
    An unresolved parent, an unscored child, or a legacy outcome gives empty `fixed` and `broke` lists and a null `validation_delta`.
    """
    reflections = [item for item in outcomes if is_reflection(item)]
    identities = {seed[0]: seed[1]}
    for item in reflections:
        key, proposed = _text(item.get("components_digest")), _text(item.get("proposed"))
        if key is not None and proposed is not None:
            _ = identities.setdefault(key, proposed)
    train, scored = _newest(outcomes, "train"), _validation(outcomes, validation)
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
            was, now = _mean(scored.get(parent), validation), _mean(scored.get(child), validation)
            delta = None if was is None or now is None else now - was
        found.append({"index": index, "parent": parent, "candidate": child, "components": _text(item.get("components_digest")),
                      "hypothesis": _text(item.get("hypothesis")), "fixed": fixed, "broke": broke,
                      "validation_delta": delta, "accepted": child is not None, "rejected": _text(item.get("rejected"))})
    return found


def protected(outcomes: Sequence[Mapping[str, object]], identity: str) -> list[str]:
    """Return the train case ids on which candidate `identity` has a full score, sorted."""
    return sorted(case for case, score in _newest(outcomes, "train").get(identity, {}).items() if score >= 1.0)


def _flat(name: str) -> str:
    return " ".join(name.split())


def _list(names: Sequence[str]) -> str:
    if not names:
        return "none"
    return ", ".join(_flat(name) for name in names[:MAX_IDS]) + (f" and {len(names) - MAX_IDS} more" if len(names) > MAX_IDS else "")


def _line(attempt: Mapping[str, object]) -> str:
    hypothesis = _text(attempt.get("hypothesis"))
    delta = attempt.get("validation_delta")
    rejected = _text(attempt.get("rejected"))
    if rejected is not None:
        result = "rejected, " + " ".join(rejected.split())[:MAX_REASON]
    else:
        result = "admitted" if attempt.get("candidate") is not None else "unknown"
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
    keep = [_flat(name) for name in protect[:MAX_PROTECTED]]

    omitted = f"{len(history)} earlier attempts omitted for length." if history else "No attempt yet."

    def compose() -> str:
        parts = [INTRO + (" ".join(lines) if lines else omitted)]
        if keep:
            parts.append("Keep these train cases passing, because the current best passes them: " + ", ".join(keep) + ".")
        return " ".join([*parts, RULES])

    while len(compose()) > LIMIT and lines:
        _ = lines.pop()
    while len(compose()) > LIMIT and keep:
        _ = keep.pop()
    return compose()
