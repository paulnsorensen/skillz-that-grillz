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

from skillz_experiments._candidate import NEW_CLI
from skillz_experiments._cases import mapping
from skillz_experiments._new_cli import prompt_rule
from skillz_experiments.search._engine import Edit

_STE_RULE = ("Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
             "active voice, present tense, one instruction per sentence, at most 20 words per procedural sentence, "
             "and at most 25 words per descriptive sentence. ")


_FROMARGS_RULE = ("The fromargs package is the default for a wedged Python source. "
                  "Use one parser. "
                  "Print JSON errors on stderr. "
                  "Exit with code 2 for a usage error and code 3 for a failed input contract. "
                  "Print only results on stdout. "
                  "A non-Python helper keeps its language. "
                  "A Python helper may use a stdlib `argparse` helper only when the skill has no wedge.toml "
                  "that can build a fromargs target. ")

MAX_EDITS = 6
MIN_EDITS = 1
LAST_PROPOSAL_FROM = 0.85


@dataclass(frozen=True)
class Progress:
    """Where the search stands. The numbers come from the session record, never from case content.

    `planned` is the search's real allowance: the planned calls less the last iteration's overshoot margin.
    """
    spent: int
    planned: int
    proposal: int

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
                f"({self.percent}%).")
        if self.fraction >= LAST_PROPOSAL_FROM:
            text += " This may be the last proposal, so prefer one small, safe change."
        return text


def request(edit: Edit, candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
            components: list[str], *, progress: Progress, cheaper: bool = False) -> tuple[str, dict[str, object]]:
    """Return the reflection prompt and the JSON schema of the answer."""
    schema: dict[str, object] = {"type": "object",
        "properties": {key: {"type": "string"} for key in components},
        "required": components, "additionalProperties": False}
    if edit == "prose+cli":
        instruction = ("Improve the supplied skill text and helper script components. "
                       + "Keep each script's command-line contract and output format. " + _FROMARGS_RULE)
        if NEW_CLI in components:
            instruction += prompt_rule()
    else:
        instruction = "Improve only the supplied skill text components. Preserve the helper CLI contract. "
    head = instruction + _STE_RULE + "Return complete component contents. Do not alter independent checks or permissions."
    if cheaper:
        head += " Prefer fewer tokens when correctness is equal."
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


def accept(answer: object, components: list[str], candidate: Mapping[str, str]) -> tuple[dict[str, str], int]:
    """Return `answer` as a component map with its edit count against `candidate`.

    Raise `ValueError` when its keys differ from `components` or a value is not text. The size never rejects it.
    """
    proposal = mapping(answer)
    if set(proposal) != set(components) or not all(isinstance(value, str) for value in proposal.values()):
        raise ValueError("reflection keys differ from the frozen components")
    texts = cast(dict[str, str], proposal)
    return texts, edit_count(candidate, texts)
