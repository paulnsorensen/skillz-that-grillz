"""The proposer seam: build the reflection request and check the answer. `run/` makes the model call.

The prompt text is frozen with the run. A change here starts a new run.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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


def request(edit: Edit, candidate: dict[str, str], feedback: Mapping[str, Sequence[Mapping[str, object]]],
            components: list[str], *, cheaper: bool = False) -> tuple[str, dict[str, object]]:
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
    return head + "\n" + json.dumps({"candidate": candidate, "feedback": feedback}, default=str), schema


def accept(answer: object, components: list[str]) -> dict[str, str]:
    """Return `answer` as a component map. Raise `ValueError` when its keys differ from `components` or a value is not text."""
    proposal = mapping(answer)
    if set(proposal) != set(components) or not all(isinstance(value, str) for value in proposal.values()):
        raise ValueError("reflection keys differ from the frozen components")
    return cast(dict[str, str], proposal)
