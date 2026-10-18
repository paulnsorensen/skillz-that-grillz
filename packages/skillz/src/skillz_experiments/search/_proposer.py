"""The proposer seam: build the reflection request and check the answer. `run/` makes the model call.

The prompt text is frozen with the run. A change here starts a new run.
"""
from __future__ import annotations

import difflib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from skillz_experiments._candidate import HYPOTHESIS, NEW_CLI, NOTES, WEDGE_PREFIX
from skillz_experiments._cases import mapping
from skillz_experiments._new_cli import prompt_rule
from skillz_experiments.search._engine import Edit
from skillz_experiments.search._history import render, render_earlier, render_notes, render_rejected

_STE_RULE = ("Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
             "active voice, present tense, one instruction per sentence, at most 20 words per procedural sentence, "
             "and at most 25 words per descriptive sentence. ")


_CLI_RULE = ("Improve the supplied skill text and helper script components. "
             "You may change a script's command-line interface when the change helps the skill. "
             "When you change an interface, update every caller and every skill text reference to match. "
             "A non-Python helper keeps its language. "
             "Write only skill content into a component. "
             "Do not put facts about this prompt, the search, or the host into a component. ")

# Only a run that offers a wedge source or `@new-cli` sends this rule, so a target with no wedge gets no wedge text.
_FROMARGS_RULE = ("The fromargs package is the default for a wedged Python source. "
                  "Use one parser. "
                  "Print JSON errors on stderr. "
                  "Exit with code 2 for a usage error and code 3 for a failed input contract. "
                  "Print only results on stdout. "
                  "A Python helper may use a stdlib `argparse` helper only when the skill has no wedge.toml "
                  "that can build a fromargs target. ")

MAX_HYPOTHESIS = 200
HYPOTHESIS_TOO_LONG = "hypothesis-too-long"
HYPOTHESIS_NOT_TEXT = "hypothesis-not-text"
MAX_NOTES = 500
NOTES_TOO_LONG = "notes-too-long"
NOTES_NOT_TEXT = "notes-not-text"

MAX_EDITS = 6
MIN_EDITS = 1
LAST_PROPOSAL_FROM = 0.85


@dataclass(frozen=True)
class Progress:
    """Where the search stands. The numbers come from the session record, never from case content.

    `planned` is the search's real allowance: the planned calls less the last iteration's overshoot margin.
    `stalled` and `advice` come from the stall signal.
    """
    spent: int
    planned: int
    proposal: int
    stalled: int = 0
    advice: str = ""

    @property
    def fraction(self) -> float:
        """Return search calls spent over search calls planned, clamped to [0, 1]."""
        return 1.0 if self.planned < 1 else min(1.0, max(0.0, self.spent / self.planned))

    def budget(self) -> int:
        """Return the edit budget: a cosine schedule from `MAX_EDITS` at the start to `MIN_EDITS` at the end, rounded down."""
        return math.floor(MIN_EDITS + (MAX_EDITS - MIN_EDITS) * 0.5 * (1 + math.cos(math.pi * self.fraction)))

    @property
    def percent(self) -> int:
        """Return the spent share as a whole percent, rounded down with integer math."""
        return 100 if self.planned < 1 else min(100, 100 * max(0, self.spent) // self.planned)

    def status(self) -> str:
        """Return the budget sentence and the run-status block."""
        count = self.budget()
        text = (f"Make at most {count} {'edit' if count == 1 else 'edits'} across all components. "
                "An edit is one block of consecutive changed lines. "
                f"Run status: this is proposal {self.proposal}. Search calls spent: {self.spent} of {self.planned} "
                f"({self.percent}%). Proposals since the last validation gain: {self.stalled}.")
        if self.fraction >= LAST_PROPOSAL_FROM:
            text += " This may be the last proposal, so prefer one small, safe change."
        if self.advice:
            text += " " + self.advice
        return text


def request(edit: Edit, candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
            components: list[str], *, progress: Progress, cheaper: bool = False,
            history: Sequence[Mapping[str, object]] = (), protect: Sequence[str] = (),
            memory: Sequence[Mapping[str, object]] = (), journal: Sequence[Mapping[str, object]] = (),
            earlier: Sequence[Mapping[str, object]] = ()) -> tuple[str, dict[str, object]]:
    """Return the reflection prompt and the JSON schema of the answer.

    `history` holds the attempts, oldest first. `protect` holds the train case ids that the current best passes.
    `memory` holds the rejected memory, newest first. `journal` holds the proposer notes, oldest first.
    `earlier` holds the attempts of compatible earlier runs, oldest first.
    The framework facts (history, rejected memory, and earlier runs) and the proposer notes are separate sections.
    """
    nullable: dict[str, object] = {"type": ["string", "null"]}
    schema: dict[str, object] = {"type": "object",
        "properties": {key: {"type": "string"} for key in components} | {HYPOTHESIS: nullable, NOTES: dict(nullable)},
        "required": [*components, HYPOTHESIS, NOTES], "additionalProperties": False}
    if edit == "prose+cli":
        instruction = _CLI_RULE
        if NEW_CLI in components or any(name.startswith(WEDGE_PREFIX) for name in components):
            instruction += _FROMARGS_RULE
        if NEW_CLI in components:
            instruction += prompt_rule()
    else:
        instruction = "Improve only the supplied skill text components. Preserve the helper CLI contract. "
    head = instruction + _STE_RULE + "Return complete component contents. Do not alter independent checks or permissions."
    if cheaper:
        head += " Prefer fewer tokens when correctness is equal."
    head += " Add an " + HYPOTHESIS + " text of at most " + str(MAX_HYPOTHESIS) + " characters that states why the change helps, or null."
    head += " Add an " + NOTES + " text of at most " + str(MAX_NOTES) + " characters that you want to read in later proposals, or null. "
    sections = (render(history, protect), render_rejected(memory), render_earlier(earlier), render_notes(journal))
    head += " ".join(part for part in sections if part)
    head += " " + progress.status()
    return head + "\n" + json.dumps({"candidate": candidate, "feedback": feedback}, default=str), schema


def edit_count(before: Mapping[str, str], after: Mapping[str, str]) -> int:
    """Return the edits from `before` to `after`: the blocks of changed lines, summed over the components."""
    total = 0
    for name, text in after.items():
        old, new = before.get(name, "").splitlines(keepends=True), text.splitlines(keepends=True)
        opcodes = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
        total += sum(1 for changed, _ in itertools.groupby(opcodes, key=lambda code: code[0] != "equal") if changed)
    return total


def accept(answer: object, components: list[str], candidate: Mapping[str, str]) -> tuple[dict[str, str], int, str | None, str | None]:
    """Return `answer` as a component map, its edit count against `candidate`, its hypothesis, and why the hypothesis dropped.

    Raise `ValueError` when its keys differ from `components` or a value is not text. The size never rejects it.
    A missing or null hypothesis is None. A non-text one is None with `HYPOTHESIS_NOT_TEXT`. One over `MAX_HYPOTHESIS` characters is None with `HYPOTHESIS_TOO_LONG`.
    """
    proposal = dict(mapping(answer))
    hypothesis, dropped = _free_text(proposal.pop(HYPOTHESIS, None), MAX_HYPOTHESIS, HYPOTHESIS_TOO_LONG, HYPOTHESIS_NOT_TEXT)
    _ = proposal.pop(NOTES, None)
    if set(proposal) != set(components) or not all(isinstance(value, str) for value in proposal.values()):
        raise ValueError("reflection keys differ from the frozen components")
    texts = cast(dict[str, str], proposal)
    return texts, edit_count(candidate, texts), hypothesis, dropped


def notes(answer: object) -> tuple[str | None, str | None]:
    """Return the `@notes` text of an accepted `answer` and why it dropped, by the rules of the hypothesis.

    A missing or null value is None. A non-text one is None with `NOTES_NOT_TEXT`. One over `MAX_NOTES` characters is None with `NOTES_TOO_LONG`.
    """
    return _free_text(mapping(answer).get(NOTES), MAX_NOTES, NOTES_TOO_LONG, NOTES_NOT_TEXT)


def _free_text(said: object, limit: int, too_long: str, not_text: str) -> tuple[str | None, str | None]:
    """Return a stripped proposer text and why it dropped. Blank text is None with no reason."""
    text = (said.strip() or None) if isinstance(said, str) else None
    if said is not None and not isinstance(said, str):
        return None, not_text
    if text is not None and len(text) > limit:
        return None, too_long
    return text, None
