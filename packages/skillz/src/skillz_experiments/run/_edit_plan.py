"""Choose the edit mode of a run and check the size of the editable text that each reflection call holds (gh-563).
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path

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
    prose_hint = "run with --edit prose, or " if edit == "prose+cli" and offer_prose else ""
    advice = prose_hint + "name fewer files in the contract editable list"
    return f"{excess}; each reflection call reads and returns all of it, so it can reach its per-call timeout; {advice}"


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
