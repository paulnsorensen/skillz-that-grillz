"""Write a draft journey contract for a skill. The command makes no model call."""
from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path

from skillz_experiments._cases import JOURNEY_EXPECTED_FIELDS, CodedError
from skillz_experiments._contract import LOCATION
from skillz_experiments._intake import skill_frontmatter

RUBRIC = ("Score the conversation from 0 to 100 against `reference`. The agent runs the {skill} skill for a user. "
          + "Start at 100. Subtract 20 for each item in `reference.must` that the transcript does not show. "
          + "Subtract 40 for each item in `reference.must_not` that the transcript shows. "
          + "Subtract 20 when the agent asks a question that is not in `reference.questions`. "
          + "Subtract 20 when the conversation does not end as `reference.final_stop` says. Do not go below 0.")
NEXT = ("Draft the journey cases in RUN/cases.draft.json: brief, max_turns, requirements, "
        + ", ".join(f"expected.{name}" for name in JOURNEY_EXPECTED_FIELDS)
        + ". Then ask the user to approve the contract and set its status to approved.")
GATE = "evals/gates/journey.py"


def scaffold(target: Path) -> dict[str, object]:
    """Write `<target>/evals/autoimprove.json` with `status: draft` and one `journey` kind. Refuse an existing file."""
    if not target.is_dir():
        raise CodedError("usage", f"--target {target} is not a directory")
    if not (target / "SKILL.md").is_file():
        raise CodedError("usage", f"--target {target} is not a skill: it has no SKILL.md")
    if (target / "evals").is_symlink() or (target / "evals/gates").is_symlink():
        raise CodedError("usage", f"{target / 'evals'} or its gates directory is a symlink; scaffold never writes through it")
    path = target / LOCATION
    gate = target / GATE
    if os.path.lexists(path) or os.path.lexists(gate):
        raise CodedError("contract-exists", f"{path} or {gate} exists; scaffold never replaces a contract or a gate")
    skill = skill_frontmatter(target).get("name", target.name)
    source = files("skillz_experiments").joinpath("_journey_gate.py").read_text(encoding="utf-8")
    contract = {"schema_version": 1, "status": "draft", "skill": skill, "invocation": "/{skill}",
                "kinds": {"journey": {"grader": "journey", "gate": GATE, "rubric": RUBRIC.format(skill=skill)}}}
    gate.parent.mkdir(parents=True, exist_ok=True)
    with gate.open("x", encoding="utf-8") as handle:
        _ = handle.write(source)
    with path.open("x", encoding="utf-8") as handle:
        _ = handle.write(json.dumps(contract, indent=2) + "\n")
    return {"contract": str(path), "gate": str(gate), "status": "draft", "next": NEXT}
