"""Readers and builders for search and holdout outcome rows, the search fault rules, and the stall observations.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from typing import TypeVar, cast

from skillz_experiments._budget import CallTimeout
from skillz_experiments._graders import DIAGNOSTIC_LIMIT
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import MET_LEVEL, Case, CodedError, Criterion, case_criteria
from skillz_experiments._redact import redact
from skillz_experiments.evaluation import task_tokens
from skillz_experiments.gate import close
from skillz_experiments.search import complete_means, is_reflection, is_search_validation, unit_score, validation_complete, HarnessText

# The search stops with `search-faulted` when at least this many search evaluations finished and more than half failed,
# or when this many of the newest ones failed in a row. The values are defaults, not measured limits.
FAULT_MIN_FINISHED = 4
FAULT_STREAK = 5
_FAULT_REASON = re.compile(r"\bfails: ([a-z][a-z-]*)")
# The feedback caps each criterion text and judge reason, and caps the sum of those fields over all unmet criteria.
FIELD_LIMIT = 300
FIELDS_LIMIT = 4_000
TRUNCATED = "[truncated]"
# A file is in `edit_hint` when it shares a run of this many normalized words with an unmet criterion text.
HINT_WORDS = 4
NO_HINT = "unknown"
_EDGES = re.compile(r"^[\W_]+|[\W_]+$")


def failed_outcome(case: Case, candidate: Candidate, error: Exception, *, arm: str = "search",
                   repeat: int | None = None) -> dict[str, object]:
    """Return the outcome that records a failed evaluation. Its 0.0 score is a placeholder that no decision reads.

    A holdout or replay failure names its arm and repeat.
    """
    outcome: dict[str, object] = {"arm": arm, "split": case.split, "case_id": case.identifier, "score": 0.0,
                                  "status": "evaluation-failed", "failure": str(error)}
    outcome["reason"] = fault_reason(error)
    if arm == "search":
        outcome["search_candidate"] = candidate.identity
    if repeat is not None:
        outcome["repeat"] = repeat
    return outcome


def fault_reason(error: Exception) -> str:
    """Return the stable reason of a failed evaluation: `timeout`, the reason that an adapter names after `fails:`, else `error`."""
    if isinstance(error, CallTimeout):
        return "timeout"
    match = _FAULT_REASON.search(str(error))
    return match.group(1) if match else "error"


def is_failed(item: Mapping[str, object]) -> bool:
    return item.get("status") == "evaluation-failed"


def reason_counts(failed: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return dict(sorted(Counter(str(item.get("reason", "error")) for item in failed).items()))


def search_counts(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Return the scored and failed counts of the search evaluations in `rows`, and the failed counts by reason."""
    failed = [item for item in rows if is_failed(item)]
    return {"scored": len(rows) - len(failed), "failed": len(failed), "failed_by_reason": reason_counts(failed)}


def search_fault(rows: Sequence[Mapping[str, object]]) -> CodedError | None:
    """Return `search-faulted` when the search evaluations in `rows` fail in a majority or in a streak, else None."""
    failed = [item for item in rows if is_failed(item)]
    streak = 0
    for item in reversed(rows):
        if not is_failed(item):
            break
        streak += 1
    majority = len(rows) >= FAULT_MIN_FINISHED and 2 * len(failed) > len(rows)
    if not (majority or streak >= FAULT_STREAK):
        return None
    return CodedError("search-faulted", f"{len(failed)} of {len(rows)} search evaluations failed ({reason_counts(failed)}; "
                      + f"the newest: {failed[-1].get('failure')}); wait for the provider, then resume the run in the same directory")


def reflection_fault(rows: Sequence[Mapping[str, object]]) -> CodedError | None:
    """Return `search-faulted` when the newest `FAULT_STREAK` reflection calls all failed, else None."""
    newest = rows[-FAULT_STREAK:]
    if len(newest) < FAULT_STREAK or any(item.get("status") != "failed" for item in newest):
        return None
    return CodedError("search-faulted", f"{FAULT_STREAK} reflection calls in a row failed ({reason_counts(newest)}; "
                      + f"the newest: {newest[-1].get('failure')}); fix the cause, then resume the run in the same directory")


def rejection_detail(outcome: dict[str, object]) -> str | None:
    """Return the rejection reason of `outcome` when it is a string. A value read back from run.json can be any type."""
    detail = outcome.get("rejection")
    return detail if isinstance(detail, str) else None


def timed_out(item: Mapping[str, object]) -> bool:
    """Return whether `item` is a contract rejection by a final sandbox timeout.

    A resume scores such a holdout or replay case again. A search outcome of this kind stays final for the run.
    """
    return item.get("status") == "candidate-contract-rejected" and item.get("rejection_kind") == "timeout"


def holdout_unscored(item: Mapping[str, object]) -> bool:
    """Return whether a holdout outcome has no gate score: a failed evaluation or a final check timeout."""
    return is_failed(item) or timed_out(item)


Outcome = TypeVar("Outcome", bound=Mapping[str, object])


def scored_holdout(outcomes: Iterable[Outcome], arms: Collection[str]) -> Iterator[Outcome]:
    """Yield the holdout outcomes of `arms` in order.

    An `evaluation-failed` outcome and a final check timeout are unscored and drop out.
    """
    for item in outcomes:
        if item.get("split") == "holdout" and item.get("arm") in arms and not holdout_unscored(item):
            yield item


def holdout_scores(outcomes: Sequence[Mapping[str, object]], arm: str) -> dict[str, dict[int, float]]:
    """Return the scored holdout scores of `arm` by case and repeat. The newest outcome of a case and repeat wins."""
    found: dict[str, dict[int, float]] = {}
    for item in scored_holdout(outcomes, (arm,)):
        found.setdefault(str(item.get("case_id")), {})[cast(int, item.get("repeat"))] = unit_score(item["score"])
    return found


def by_repeat_order(scores: Mapping[str, Mapping[int, float]]) -> dict[str, list[float]]:
    """Return the scores of each case as a list in repeat order."""
    return {case_id: [by_repeat[repeat] for repeat in sorted(by_repeat)] for case_id, by_repeat in scores.items()}


def case_ids(feedback: Mapping[str, Sequence[Mapping[str, object]]]) -> list[str]:
    """Return the train case ids in the feedback that the proposer saw, first seen first."""
    names = (record.get("case_id") for records in feedback.values() for record in records)
    return list(dict.fromkeys(name for name in names if isinstance(name, str)))


def is_unscored(record: Mapping[str, object]) -> bool:
    """Return whether a feedback record of GEPA, or a value nested in it, marks an unscored evaluation."""
    return record.get("unscored") is True or any(isinstance(value, Mapping) and cast(Mapping[str, object], value).get("unscored") is True
                                                 for value in record.values())


def feedback_for(result: dict[str, object], case: Case, harness: HarnessText,
                 candidate: Candidate) -> tuple[float, dict[str, object]]:
    """Return the GEPA score and feedback of one search outcome. The request has its harness text masked.

    A judged outcome adds `unmet`, each criterion below level 4, and `edit_hint`, the editable files that its texts name.
    An outcome with a `gate_diagnostic` adds it as `gate_feedback`.
    """
    request = harness.mask(case.request)
    if is_failed(result):
        # GEPA needs a number. The session ignores this 0.0 in every decision and drops the record from reflection feedback.
        return 0.0, {"unscored": True, "request": request}
    score = unit_score(result["score"])
    feedback: dict[str, object] = {"task_correct": score, "request": request, "case_id": case.identifier}
    if "scores" in result:
        feedback["grader_scores"] = result["scores"]
    unmet = _unmet(result, case)
    if unmet:
        feedback["unmet"] = _bounded(unmet, harness)
        feedback["edit_hint"] = _edit_hint([criterion.text for criterion, _level, _reason in unmet], candidate)
    diagnostic = result.get("gate_diagnostic")
    if isinstance(diagnostic, str):
        feedback["gate_feedback"] = harness.mask(diagnostic)[-DIAGNOSTIC_LIMIT:]
    return score, feedback


def _unmet(result: Mapping[str, object], case: Case) -> list[tuple[Criterion, int, str]]:
    """Return each case criterion that the judge put below level 4, with its level and reason: `must` first, then by level."""
    verdicts = result.get("criteria")
    if not isinstance(verdicts, Mapping) or not verdicts:
        return []
    found: list[tuple[Criterion, int, str]] = []
    for criterion in case_criteria(case.expected, case.identifier):
        verdict = cast(Mapping[str, object], verdicts).get(criterion.id)
        if not isinstance(verdict, Mapping):
            continue
        entry = cast(Mapping[str, object], verdict)
        level, reason = entry.get("level"), entry.get("reason")
        if type(level) is int and level < MET_LEVEL:
            found.append((criterion, level, reason if isinstance(reason, str) else ""))
    return sorted(found, key=lambda item: (not item[0].must, item[1]))


def _cut(value: str, harness: HarnessText) -> str:
    """Redact the whole of `value`, mask it, and cut it to `FIELD_LIMIT` characters, the `[truncated]` marker included."""
    clean = harness.mask(redact(value))
    return clean if len(clean) <= FIELD_LIMIT else clean[:FIELD_LIMIT - len(TRUNCATED)] + TRUNCATED


def _bounded(unmet: Sequence[tuple[Criterion, int, str]], harness: HarnessText) -> list[dict[str, object]]:
    """Return the unmet entries. Each keeps its id, must flag, and level.

    Each text and reason is redacted whole, then masked, then cut to `FIELD_LIMIT` with `[truncated]` at the cut. The first
    entries keep their fields while all the fields, the markers of the cut entries included, stay within `FIELDS_LIMIT`.
    Each later entry gets `[truncated]` in both fields, or an empty text when even the markers alone pass the limit.
    """
    fields = [tuple(_cut(value, harness) for value in (criterion.text, reason)) for criterion, _level, reason in unmet]
    marker = TRUNCATED if 2 * len(TRUNCATED) * len(fields) <= FIELDS_LIMIT else ""
    kept = len(fields)
    while sum(len(text) + len(why) for text, why in fields[:kept]) + 2 * len(marker) * (len(fields) - kept) > FIELDS_LIMIT:
        kept -= 1
    return [{"id": criterion.id, "must": criterion.must, "level": level,
             "text": fields[position][0] if position < kept else marker,
             "reason": fields[position][1] if position < kept else marker}
            for position, (criterion, level, _reason) in enumerate(unmet)]


def _runs(text: str) -> set[tuple[str, ...]]:
    """Return each run of `HINT_WORDS` words in `text`, lower-cased with edge punctuation stripped."""
    words = [word for word in (_EDGES.sub("", raw) for raw in text.lower().split()) if word]
    return {tuple(words[start:start + HINT_WORDS]) for start in range(len(words) - HINT_WORDS + 1)}


def _edit_hint(texts: Sequence[str], candidate: Candidate) -> list[str]:
    """Return the sorted editable files that share a word run with any of `texts`, else `["unknown"]`.

    The hint is advice only. It never changes which files the proposer may edit.
    """
    wanted = set[tuple[str, ...]]().union(*map(_runs, texts))
    named = sorted(name for name in candidate.editable if _runs(candidate.files.get(name, "")) & wanted)
    return named or [NO_HINT]


def repeat_feedback(results: Sequence[dict[str, object]], case: Case, harness: HarnessText,
                    candidate: Candidate) -> tuple[float, dict[str, object]]:
    """Return the GEPA score and feedback of the repeats of one case: the mean of the scored repeats.

    One outcome keeps its own feedback. A case with no scored repeat takes the unscored path of a failed outcome.
    The mean of repeats, a validation case, gets the masked request and no failure evidence: GEPA reflects on train cases.
    """
    scored = [unit_score(item["score"]) for item in results if not is_failed(item)]
    if len(results) == 1 or not scored:
        return feedback_for(results[-1], case, harness, candidate)
    mean = math.fsum(scored) / len(scored)
    return mean, {"task_correct": mean, "request": harness.mask(case.request), "case_id": case.identifier}

def mean_tokens(counts: Sequence[int | None]) -> float | None:
    """Return the mean of `counts`, or None when it is empty, lacks a count, or overflows a float."""
    known = [count for count in counts if count is not None]
    if not counts or len(known) != len(counts):
        return None
    try:
        return math.fsum(known) / len(known)
    except OverflowError:
        return None


def stall_observations(outcomes: Sequence[Mapping[str, object]], validation: set[str],
                       repeats: int = 1, *, cheaper: bool = False) -> list[float | None]:
    """Return one stall observation per recorded proposal, oldest first.

    The search outcomes after a reflection and before the next one belong to its `proposed` candidate. The observation
    is the rise of that candidate's validation mean over the best mean of every earlier candidate, the seed included.
    A mean needs `validation_complete` with `repeats` runs per case, and a case scores the mean of its scored repeats.
    It is None when an evaluation of the candidate failed or the host build of the proposal failed. It is 0.0 when the
    proposal has no identity or its candidate does not complete the validation set.
    `cheaper` is True when the run sets a token field. Then a proposal with a validation mean equal to the best earlier
    mean and known mean validation task tokens below those of the cheapest best earlier candidate has the observation
    `inf`, a rise above every band, so it counts as progress.
    """
    marks = [index for index, item in enumerate(outcomes) if is_reflection(item)]
    observations: list[float | None] = []
    for start, end in zip(marks, [*marks[1:], len(outcomes)]):
        before = outcomes[:start]
        means = complete_means(before, validation, repeats)
        best = max(means.values(), default=0.0)
        rival = _cheapest_best(before, means, best) if cheaper else None
        window = outcomes[start + 1:end]
        reflection = outcomes[start]
        proposed = reflection.get("proposed")
        earlier = [item for item in before if is_search_validation(item) and not is_failed(item)
                   and item["search_candidate"] == proposed]
        observations.append(None if reflection.get("status") is not None
                            else _observation(window, proposed, validation, best, earlier, repeats, rival))
    return observations


def _observation(window: Sequence[Mapping[str, object]], identity: object, validation: set[str],
                 best: float, earlier: Sequence[Mapping[str, object]], repeats: int,
                 rival: float | None = None) -> float | None:
    """Return the observation of one proposal from the outcomes in its window.

    Only the evaluations of the proposed candidate count, up to the end of its validation set. GEPA can score the
    candidate again as the next parent before the next reflection. That later score does not change the observation.
    `earlier` holds the scored validation outcomes of the candidate from before the window. A resume reuses those
    outcomes, so the window does not repeat them.
    `rival` is the lowest known mean validation task tokens among the best earlier candidates, or None.
    """
    if not isinstance(identity, str):
        return 0.0
    seen = list(earlier)
    for item in window:
        if validation_complete(seen, identity, validation, repeats):
            break
        if item.get("search_candidate") != identity:
            continue
        if is_failed(item):
            return None
        if item.get("split") == "validation":
            seen.append(item)
    mean = complete_means(seen, validation, repeats).get(identity)
    if mean is None:
        return 0.0
    own = _validation_tokens(seen, identity)
    if rival is not None and own is not None and close(mean, best) and own < rival:
        return math.inf
    return max(0.0, mean - best)


def _validation_tokens(outcomes: Sequence[Mapping[str, object]], identity: str) -> float | None:
    """Return the mean task tokens of the scored validation outcomes of `identity`, or None when a count is unknown."""
    return mean_tokens([task_tokens(item) for item in outcomes if is_search_validation(item) and not is_failed(item)
                        and item["search_candidate"] == identity])


def _cheapest_best(outcomes: Sequence[Mapping[str, object]], means: Mapping[str, float], best: float) -> float | None:
    """Return the lowest known validation task tokens among the candidates whose mean equals `best`, or None."""
    counts = [_validation_tokens(outcomes, identity) for identity, mean in means.items() if close(mean, best)]
    return min((count for count in counts if count is not None), default=None)


