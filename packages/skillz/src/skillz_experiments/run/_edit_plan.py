"""Choose the edit mode of a run and check the size of the editable text that each reflection call holds (gh-563).

Also show the reflection time that this size implies in the run estimate (gh-571).
"""
from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from skillz_experiments._budget import PROMPT_CHARS_PER_SECOND, SECONDS_PER_CALL, Estimate, ceilings, estimate
from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import CodedError
from skillz_experiments._contract import Contract
from skillz_experiments.run._plan import editable_files
from skillz_experiments.run._wedge_targets import TargetPlan, plan_targets
from skillz_experiments.search import Edit

REFLECTION_TEXT_LIMIT = 200_000
"""The editable characters above which a reflection call risks its per-call timeout (gh-563).

A reflection prompt holds every editable file, and the answer returns every component in full.
At `PROMPT_CHARS_PER_SECOND` in `_budget`, a prompt at this limit adds 1000 seconds to the per-call timeout.
Change the two values together.
"""


def editable_chars(files: Mapping[str, str], editable: Sequence[str]) -> int:
    """Return the characters of the editable files, which every reflection prompt holds."""
    return sum(len(files.get(name, "")) for name in editable)


def reflection_excess(chars: int) -> str | None:
    """Return the clause that names `chars` editable characters over `REFLECTION_TEXT_LIMIT`, or None at or below it."""
    if chars <= REFLECTION_TEXT_LIMIT:
        return None
    return f"the editable text holds {chars} characters, more than the reflection limit of {REFLECTION_TEXT_LIMIT}"


def reflection_warning(chars: int, edit: Edit, *, offer_prose: bool = True) -> str | None:
    """Return the budget-question warning for `chars` editable characters, or None at or below the limit.

    The advice names `--edit prose` only for a `prose+cli` edit, and only when `offer_prose` is true.
    """
    excess = reflection_excess(chars)
    if excess is None:
        return None
    return f"{excess}; each reflection call reads and returns all of it, so it can reach its per-call timeout; {_advice(edit, offer_prose)}"


def _advice(edit: Edit, offer_prose: bool) -> str:
    prose_hint = "run with --edit prose, or " if edit == "prose+cli" and offer_prose else ""
    return prose_hint + "name fewer files in the contract editable list"


def reflection_time(reflection_calls: int, chars: int, sized: Estimate, *,
                    advice: str | None = None) -> tuple[dict[str, int], str | None]:
    """Return the `estimate.reflection` block and the `reflection_time_warning`, or None for the warning (gh-571).

    A reflection prompt holds the `chars` editable characters, and its answer returns them. So each reflection call
    takes `SECONDS_PER_CALL` plus 1 second per `PROMPT_CHARS_PER_SECOND` characters, and the calls run one at a time.
    The projected run seconds put that time in place of the flat reflection share that `estimate` adds to
    `sized.seconds`. The warning fires when the projection exceeds the approved seconds cap. It ends with `advice`
    when that is not None. Neither value changes the search size or the budget.
    """
    seconds = reflection_calls * (SECONDS_PER_CALL + math.ceil(chars / PROMPT_CHARS_PER_SECOND))
    projected = sized.seconds - estimate(reflection_calls=reflection_calls).seconds + seconds
    cap = ceilings(sized.journey)[1]
    block = {"calls": reflection_calls, "editable_chars": chars, "seconds": seconds}
    if projected <= cap:
        return block, None
    tail = "" if advice is None else f"; {advice}"
    return block, (f"{reflection_calls} reflection calls over {chars} editable characters need about {seconds} seconds, "
                   + f"so the run needs about {projected} of its {cap} seconds, and the search can stop at the deadline "
                   + f"before it spends its calls{tail}")


@dataclass
class ReflectionNotes:
    """The reflection warnings of one sized run, keyed by their `budget-unapproved` stop key (gh-563, gh-571).

    `size` writes the size warning before `size_plan` runs, so the warning reaches stderr before a sizing stop.
    `time` then adds the time block and its warning. `_add` writes each warning to stderr at once, so an approved
    run shows it too. When both warnings fire, only the size warning gives the advice.
    """

    chars: int
    edit: Edit
    offer_prose: bool
    warnings: dict[str, str] = field(default_factory=dict[str, str])

    @classmethod
    def size(cls, seed: Candidate, edit: Edit, *, offer_prose: bool) -> ReflectionNotes:
        """Return the notes of `seed` with its size warning, if any."""
        notes = cls(editable_chars(seed.files, seed.editable), edit, offer_prose)
        notes._add("reflection_warning", reflection_warning(notes.chars, edit, offer_prose=offer_prose))
        return notes

    def time(self, reflection_calls: int, sized: Estimate) -> dict[str, int]:
        """Return the `estimate.reflection` block of the plan, and add its time warning, if any."""
        advice = None if "reflection_warning" in self.warnings else _advice(self.edit, self.offer_prose)
        block, slow = reflection_time(reflection_calls, self.chars, sized, advice=advice)
        self._add("reflection_time_warning", slow)
        return block

    def _add(self, key: str, text: str | None) -> None:
        if text is not None:
            self.warnings[key] = text
            print(f"skillz-experiment: warning: {text}", file=sys.stderr)


def _fall_back_to_prose(target: Path, skeleton: Candidate, contract: Contract,
                        reason: str) -> tuple[TargetPlan, Candidate]:
    """Plan an omitted `--edit` as `prose` after `prose+cli` could not plan, and say why on stderr."""
    wedge = plan_targets(target, skeleton.files, contract, "prose")
    files = skeleton.files | wedge.sources
    frozen = skeleton.frozen | wedge.frozen
    planned = wedge, Candidate(files, tuple(editable_files(files, contract, "prose", frozen.keys(), wedge)), contract, frozen=frozen)
    # Print only after the prose plan succeeds: the size fallback suppresses a failed plan and keeps prose+cli.
    print(f"skillz-experiment: {reason}; this run uses --edit prose", file=sys.stderr)
    return planned


def seed_or_prose(target: Path, skeleton: Candidate, contract: Contract, edit: Edit | None, mode: Edit, wedge: TargetPlan,
                  files: dict[str, str], frozen: dict[str, bytes]) -> tuple[Edit, TargetPlan, Candidate, bool]:
    """Build the seed candidate. Without an explicit `--edit`, `prose+cli` falls back to `prose`.

    The fallback happens when no helper is editable, or when the editable text exceeds `REFLECTION_TEXT_LIMIT`
    and the prose set can be planned. The last item is true when the run fell back to `prose`.
    """
    try:
        seed = Candidate(files, tuple(editable_files(files, contract, mode, frozen.keys(), wedge)), contract, frozen=frozen)
    except CodedError as error:
        if edit is not None or error.code != "helper-missing":
            raise
        return ("prose", *_fall_back_to_prose(target, skeleton, contract, str(error)), True)
    reason = reflection_excess(editable_chars(seed.files, seed.editable))
    if edit is None and mode == "prose+cli" and reason is not None:
        # A prose plan that fails, such as explicit non-Markdown editable names, keeps prose+cli and its warning.
        with suppress(CodedError):
            return ("prose", *_fall_back_to_prose(target, skeleton, contract, f"the prose+cli plan: {reason}"), True)
    return mode, wedge, seed, False
