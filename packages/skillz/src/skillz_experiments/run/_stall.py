"""Validation means and the stall observations that the recorded search outcomes give.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import cast

from skillz_experiments.run._outcomes import is_failed
from skillz_experiments.run._record_fields import unit_score
from skillz_experiments.search import is_reflection


def mean_tokens(counts: Sequence[int | None]) -> float | None:
    """Return the mean of `counts`, or None when it is empty, lacks a count, or overflows a float."""
    known = [count for count in counts if count is not None]
    if not counts or len(known) != len(counts):
        return None
    try:
        return math.fsum(known) / len(known)
    except OverflowError:
        return None


def stall_observations(outcomes: Sequence[Mapping[str, object]], validation: set[str]) -> list[float | None]:
    """Return one stall observation per recorded proposal, oldest first.

    The search outcomes after a reflection and before the next one belong to its `proposed` candidate. The observation
    is the rise of that candidate's validation mean over the best mean of every earlier candidate, the seed included.
    It is None when an evaluation of the candidate failed or the host build of the proposal failed. It is 0.0 when the
    proposal has no identity or its candidate does not complete the validation set.
    """
    marks = [index for index, item in enumerate(outcomes) if is_reflection(item)]
    seen = add_validation_scores({}, outcomes[:marks[0]] if marks else [])
    observations: list[float | None] = []
    for start, end in zip(marks, [*marks[1:], len(outcomes)]):
        best = max(complete_means(seen, validation).values(), default=0.0)
        window = outcomes[start + 1:end]
        reflection = outcomes[start]
        proposed = reflection.get("proposed")
        earlier = seen.get(proposed, {}) if isinstance(proposed, str) else {}
        observations.append(None if reflection.get("status") is not None
                            else _observation(window, proposed, validation, best, earlier))
        _ = add_validation_scores(seen, window)
    return observations


def _observation(window: Sequence[Mapping[str, object]], identity: object, validation: set[str],
                 best: float, earlier: Mapping[str, float]) -> float | None:
    """Return the observation of one proposal from the outcomes in its window.

    Only the evaluations of the proposed candidate count, up to the end of its validation set. GEPA can score the
    candidate again as the next parent before the next reflection. That later score does not change the observation.
    `earlier` holds the validation scores of the candidate from before the window. A resume reuses those scores, so
    the window does not repeat them.
    """
    scores = dict(earlier)
    for item in window:
        if identity is None or set(scores) == validation:
            break
        if item.get("search_candidate") != identity:
            continue
        if is_failed(item):
            return None
        if item.get("split") == "validation":
            scores[cast(str, item["case_id"])] = unit_score(item["score"])
    if set(scores) != validation:
        return 0.0
    return max(0.0, math.fsum(scores.values()) / len(scores) - best)


def is_search_validation(item: Mapping[str, object]) -> bool:
    return item.get("arm") == "search" and item.get("split") == "validation" and "search_candidate" in item


def add_validation_scores(scores: dict[str, dict[str, float]],
                          items: Sequence[Mapping[str, object]]) -> dict[str, dict[str, float]]:
    """Add each search validation score in `items` to `scores` by candidate and case, and return `scores`.

    A later score for a case replaces the earlier one. An `evaluation-failed` outcome has no score and adds none.
    """
    for item in items:
        if is_search_validation(item) and not is_failed(item):
            scores.setdefault(cast(str, item["search_candidate"]), {})[cast(str, item["case_id"])] = unit_score(item["score"])
    return scores


def complete_means(scores: Mapping[str, Mapping[str, float]], validation: set[str]) -> dict[str, float]:
    """Return the mean score of each candidate that has a score on every validation case."""
    return {identity: math.fsum(by_case.values()) / len(by_case) for identity, by_case in scores.items()
            if set(by_case) == validation}
