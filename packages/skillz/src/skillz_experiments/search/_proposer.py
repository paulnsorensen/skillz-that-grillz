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

# The failure evidence of a feedback record, in the order that the prompt shows it, after every other key.
EVIDENCE = ("unmet", "edit_hint", "gate_feedback")
EVIDENCE_RULE = ("A feedback record can end with unmet, the criteria that the judge did not fully meet. "
                 "Then edit_hint names the files that probably need the change. "
                 "Then gate_feedback holds the end of the failed gate output. "
                 "The edit_hint can be wrong. Every editable component stays editable. "
                 "The gate is the independent check of the case. "
                 "When a skill rule conflicts with gate_feedback, change the skill rule to match the gate.")

MAX_EDITS = 6
MIN_EDITS = 1
CLI_EDITS = 2
"""A new `@new-cli` target and the SKILL.md line that calls it take 2 edits. The budget keeps them while `@new-cli` is empty."""
LAST_PROPOSAL_FROM = 0.85
# Only a run that offers `@new-cli` sends this rule. A prose fix of a mechanical rule can pass, so the search tries code early.
# The proposer cannot read the fromargs source, so the rule gives its API. A proposer with no API wrote no target.
_CLI_EARLY = (f"A rule that code applies the same way to every input is a good fit for {NEW_CLI}. "
              + f"Try {NEW_CLI} early. Do not wait until a prose fix fails. "
              + "A CLI can also cut the task tokens of each case. "
              + "The fromargs source is not in this prompt. Use this summary of its API: "
              + '`from fromargs import App`, `app = App("<name>")`, one handler under `@app.default`, '
              + "and `main = app.main`. "
              + "The handler takes typed parameters and returns JSON data. fromargs prints that data on stdout. "
              + "A module that uses only the standard library is valid too. ")
# Only a run with a token rule sends this rule. That run adds these keys to a feedback record when the counts are known.
TOKEN_FIELDS_RULE = ("A feedback record can hold task_tokens, the mean uncached task tokens of the candidate on that case, "
                     + "and incumbent_task_tokens, the mean of the current best candidate on the same case.")


@dataclass(frozen=True)
class Progress:
    """Where the search stands. The numbers come from the session record, never from case content.

    `planned` is the search's real allowance: the planned calls less the last iteration's overshoot margin.
    `stalled` and `advice` come from the stall signal. `cli_open` is True while the run offers an empty `@new-cli`.
    """
    spent: int
    planned: int
    proposal: int
    stalled: int = 0
    advice: str = ""
    cli_open: bool = False

    @property
    def fraction(self) -> float:
        """Return search calls spent over search calls planned, clamped to [0, 1]."""
        return 1.0 if self.planned < 1 else min(1.0, max(0.0, self.spent / self.planned))

    def budget(self) -> int:
        """Return the edit budget: a cosine schedule from `MAX_EDITS` at the start to `MIN_EDITS` at the end, rounded down.

        While `cli_open` is True, the budget is at least `CLI_EDITS`.
        """
        scheduled = math.floor(MIN_EDITS + (MAX_EDITS - MIN_EDITS) * 0.5 * (1 + math.cos(math.pi * self.fraction)))
        return max(scheduled, CLI_EDITS) if self.cli_open else scheduled

    @property
    def percent(self) -> int:
        """Return the spent share as a whole percent, rounded down with integer math."""
        return 100 if self.planned < 1 else min(100, 100 * max(0, self.spent) // self.planned)

    def status(self) -> str:
        """Return the budget sentence and the run-status block."""
        count = self.budget()
        text = (f"Make at most {count} {'edit' if count == 1 else 'edits'} across all components. "
                + "An edit is one block of consecutive changed lines. "
                + (f"A new {NEW_CLI} target and the SKILL.md line that calls it are {CLI_EDITS} edits. "
                   if self.cli_open else "")
                + f"Run status: this is proposal {self.proposal}. Search calls spent: {self.spent} of {self.planned} "
                + f"({self.percent}%). Proposals since the last validation gain: {self.stalled}.")
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
            instruction += prompt_rule() + _CLI_EARLY
    else:
        instruction = "Improve only the supplied skill text components. Preserve the helper CLI contract. "
    head = instruction + _STE_RULE + "Return complete component contents. Do not alter independent checks or permissions."
    if cheaper:
        head += " Prefer fewer tokens when correctness is equal. " + TOKEN_FIELDS_RULE
    head += " Add an " + HYPOTHESIS + " text of at most " + str(MAX_HYPOTHESIS) + " characters that states why the change helps, or null."
    head += " Add an " + NOTES + " text of at most " + str(MAX_NOTES) + " characters that you want to read in later proposals, or null. "
    sections = (render(history, protect), render_rejected(memory), render_earlier(earlier), render_notes(journal))
    head += " ".join(part for part in sections if part)
    shown = {name: [_evidence_last(record) for record in records] for name, records in feedback.items()}
    if any(key in record for records in shown.values() for record in records for key in EVIDENCE):
        head += " " + EVIDENCE_RULE
    head += " " + progress.status()
    return head + "\n" + json.dumps({"candidate": candidate, "feedback": shown}, default=str), schema


def _evidence_last(record: Mapping[str, object]) -> dict[str, object]:
    """Return `record` with its `EVIDENCE` keys moved to the end, in `EVIDENCE` order."""
    return ({key: value for key, value in record.items() if key not in EVIDENCE}
            | {key: record[key] for key in EVIDENCE if key in record})


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
