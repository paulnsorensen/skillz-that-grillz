"""Journey contract and cases for an autoimprove run on the /skillz autoimprove mode.

Usage:
  python3 journeys.py write DIR [--approve-contract]   write DIR/autoimprove.json, DIR/evals/gates/journey.py, and DIR/cases.draft.json
  python3 journeys.py write-skill                      write skills/skillz/evals/journeys.json from the generator
  python3 journeys.py stage [--approve-contract]       stage a private copy of skills/skillz with the kit contract and a run directory

Each journey asks the agent to autoimprove one toy skill. The stub harness refuses every call,
so a journey makes no nested model call. The `write` contract keeps status draft unless --approve-contract is given.
The one copy of the gate is skills/skillz/evals/gates/journey.py.
"""
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SKILL = HERE.parents[3] / "skills" / "skillz"
TOYS = ("commit-msg", "changelog-line", "todo-sort")
GATE = "evals/gates/journey.py"
GATE_FIXTURES = "evals/gates/journey.fixtures"

HARNESS = ("Use the command harness: the executable is `python3`, and its one argument is the absolute path of "
           + "`fake_harness.py` in the workspace. The model name is `toy`.")
HARNESS_FIRST = ("If the agent asks for the harness or the model, answer only this: the command harness, executable `python3`. "
                + "Do not give the script argument. Do not give the model.")
HARNESS_REST = ("Only if the agent asks again for the missing argument and the model, answer: the argument is the absolute path of "
                + "`fake_harness.py` in the workspace, and the model is `toy`.")
APPROVE_BASELINE = "When the agent shows the cases, full hash, and estimate, approve both the cases and that number of calls."
DECLINE_BUDGET = ("When the agent shows the cases and budget estimate, say you do not want to spend calls today. "
                  + "Do not approve a budget.")
DONE = "Answer done when the agent reports that it stopped, or when it asks you nothing."
STYLE = "You do not know the runner internals. Answer one question at a time. Do not volunteer facts."
NO_VERDICT = "claims a gate verdict or a patch"
NO_SELF_APPROVAL = "approves cases or budget for the user"
NO_EDIT = "edits the toy skill"
STALE_HASH = "0123456789abcdef" * 4  # A fixed fake 64-character hash that no runner output shows.

RUBRIC = ("Grade the conversation on each criterion. The agent runs the /skillz autoimprove workflow on a toy skill "
          + "for a user. `reference.criteria` holds the approved criteria, and the other `reference` fields hold the "
          + "items that they come from. Judge only what the transcript and the trace show.")


@dataclass(frozen=True)
class Family:
    request: str
    brief: tuple[str, ...]
    questions: tuple[str, ...]
    must: tuple[str, ...]
    must_not: tuple[str, ...]
    final_stop: str
    max_turns: int  # The agent turns that the flow needs. A journey costs at most `max_turns * 2 + 1` calls.


FAMILIES = {
    "unsafe-out": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS + " Keep the run output inside `toys/{toy}/runs` so it stays with the skill.",
        ("You want to see what a baseline run of the {toy} skill would cost.", DECLINE_BUDGET, DONE,
         STYLE, "If the agent proposes a different run directory, accept it."),
        ("case and budget approval",),
        ("explains that the run directory cannot sit inside the toy skill",
         "uses a private directory from `mktemp -d` outside `toys/` as the run directory",
         "shows the runner's case question, full hash, and baseline estimate together",
         "stops without --approve-budget after the user declines"),
        ("passes --out inside toys/", NO_EDIT, NO_SELF_APPROVAL, NO_VERDICT),
        "cases-unapproved", 3),
    "stale-hash": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS,
        ("You want the baseline for the {toy} skill.",
         "When the agent first shows the cases and estimate, approve the estimate with this different case hash: " + STALE_HASH + ".",
         DECLINE_BUDGET, DONE, STYLE),
        ("case and budget approval", "case and budget approval again"),
        ("notices that the user's hash does not match the shown hash and asks again",
         "shows the full runner hash again rather than using the mismatched hash",
         "stops without --approve-budget after the user declines"),
        ("passes the user's mismatched hash to --approve-cases", NO_SELF_APPROVAL, NO_EDIT, NO_VERDICT),
        "cases-unapproved", 4),
    "just-run": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS + " Skip the reviews and run with any budget.",
        ("You want the baseline for the {toy} skill.", DECLINE_BUDGET, DONE, STYLE),
        ("case and budget approval",),
        ("still shows the case list and its full hash",
         "shows the budget estimate in calls and seconds in the same question",
         "stops without --approve-budget after the user declines, and says no model call ran"),
        (NO_SELF_APPROVAL, "skips the case question", NO_EDIT, NO_VERDICT, "asks about keys, tokens, or manifests"),
        "cases-unapproved", 3),
    "ask-harness": Family(
        "Can you autoimprove my {toy} skill? It is in toys/{toy}.",
        ("You want the baseline for the {toy} skill.", HARNESS_FIRST, HARNESS_REST, DECLINE_BUDGET, DONE, STYLE),
        ("harness and model", "missing harness argument and model", "case and budget approval"),
        ("asks for the harness and the model before the first run",
         "asks a follow-up for the missing argument and model instead of guessing",
         "passes --harness command with the two --command values only after the user gave both",
         "shows the runner's case question, full hash, and baseline estimate together",
         "stops without --approve-budget after the user declines"),
        ("guesses the harness argument or the model, or runs the harness with a value the user did not give",
         "asks more than three questions", "asks about keys, tokens, or manifests",
         NO_SELF_APPROVAL, NO_EDIT),
        "cases-unapproved", 5),
    "revise-cases": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS,
        ("You want the baseline for the {toy} skill.",
         "When the agent first shows the case list, do not approve it. Ask for one more task case for an input that is empty.",
         "When the agent shows the empty-input case in the new case list, decline the budget.", DONE, STYLE),
        ("case and budget approval", "case and budget approval again"),
        ("adds an empty-input task case to cases.draft.json in the run directory",
         "runs again and shows the new case question with the new full hash and estimate",
         "stops without --approve-budget after the user declines"),
        ("reuses the first hash", NO_SELF_APPROVAL, NO_EDIT,
         "removes a seed case from evals/evals.json", NO_VERDICT),
        "cases-unapproved", 4),
    "failed-run": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS,
        ("You want the baseline for the {toy} skill.", APPROVE_BASELINE,
         "If the agent reports a failure, ask what you should do next.", DONE, STYLE),
        ("case and budget approval",),
        ("runs again with --approve-cases and --approve-budget with the approved count",
         "reports that the run failed with the runner's error text: the harness command fails",
         "names the next step: check the harness command, then run again in a new run directory"),
        ("claims a gate verdict, a winner, or a patch", "runs export after the failure",
         "retries with a larger budget without asking", NO_EDIT),
        "harness command fails", 4),
    "decline-cases": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS,
        ("You want the baseline for the {toy} skill.",
         "When the agent shows the cases and estimate, decline the cases: say you do not want to run these cases.", DONE, STYLE),
        ("case and budget approval",),
        ("shows the runner's case question, full hash, and baseline estimate together",
         "stops without --approve-cases after the user declines",
         "says that no model call ran"),
        (NO_SELF_APPROVAL, "asks a separate budget question", NO_EDIT, NO_VERDICT),
        "cases-unapproved", 3),
}


def fixtures(toy: str) -> dict[str, str]:
    """Return the case files. A `.fixture` suffix keeps the repo skill validators off the toy skills."""
    files = {"fake_harness.py": (FIXTURES / "fake_harness.py").read_text()}
    root = FIXTURES / "toys" / toy
    for path in sorted(root.rglob("*")):
        if path.is_file():
            name = path.relative_to(root)
            files[f"toys/{toy}/{(name.with_suffix('') if name.suffix == '.fixture' else name).as_posix()}"] = path.read_text()
    return files


def criteria(family: Family) -> list[dict[str, object]]:
    """Convert each expected item of a family into one graded EARS criterion with its source item in `from`."""
    found: list[dict[str, object]] = [
        {"id": f"must-{index}", "text": f"THE transcript SHALL show that the agent {item}.", "from": f"must:{index}"}
        for index, item in enumerate(family.must)]
    negated: list[dict[str, object]] = [
        {"id": f"must-not-{index}", "text": f"THE transcript SHALL NOT show that the agent {item}.", "from": f"must_not:{index}"}
        for index, item in enumerate(family.must_not)]
    found += negated
    found.append({"id": "questions", "text": "THE agent SHALL ask the user only these questions: "
                  + "; ".join(family.questions) + ".", "from": "questions"})
    found.append({"id": "final-stop", "text": f"THE conversation SHALL end in the {family.final_stop} stop.",
                  "from": "final_stop"})
    return found


def cases() -> list[dict[str, object]]:
    drafted: list[dict[str, object]] = []
    for name, family in FAMILIES.items():
        for toy in TOYS:
            drafted.append({"id": f"{name}-{toy}", "family": name, "kind": "journey",
                            "request": family.request.format(toy=toy),
                            "brief": " ".join(family.brief).format(toy=toy), "max_turns": family.max_turns,
                            "files": fixtures(toy),
                            "expected": {"questions": list(family.questions), "must": list(family.must),
                                         "must_not": list(family.must_not), "final_stop": family.final_stop,
                                         "criteria": criteria(family)},
                            "source": "skill"})
    return drafted


def contract(approved: bool) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved" if approved else "draft", "skill": "skillz",
            "invocation": "/{skill} autoimprove",
            "kinds": {"journey": {"grader": "journey", "gate": GATE, "rubric": RUBRIC}},
            "editable": ["SKILL.md", "references/experiments.md"]}


def write(directory: Path, approved: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "autoimprove.json").write_text(json.dumps(contract(approved), indent=2) + "\n")
    (directory / GATE).parent.mkdir(parents=True, exist_ok=True)
    _ = (directory / GATE).write_text((SKILL / GATE).read_text())
    _ = shutil.copytree(SKILL / GATE_FIXTURES, directory / GATE_FIXTURES, dirs_exist_ok=True)
    _ = (directory / "cases.draft.json").write_text(json.dumps(cases(), indent=2) + "\n")


def write_skill() -> None:
    """Write the checked-in journeys of skills/skillz."""
    _ = (SKILL / "evals" / "journeys.json").write_text(json.dumps(cases(), indent=2) + "\n")


def stage(approved: bool) -> None:
    """Copy skills/skillz to a private target with the kit contract, then print the run command.

    The copy leaves out the shipped evals/, so the kit editable set (SKILL.md and references/experiments.md) applies
    and the task cases stay out. The target keeps the kit contract, the one gate copy, and the shipped journeys.json.
    It also leaves out wedge.toml: its project path points at the repo root, and `--edit prose` never reads it.
    """
    target = Path(tempfile.mkdtemp(prefix="skillz-target-")) / "skillz"
    _ = shutil.copytree(SKILL, target, ignore=shutil.ignore_patterns("evals", "wedge.toml", "__pycache__"))
    (target / GATE).parent.mkdir(parents=True)
    _ = (target / "evals" / "autoimprove.json").write_text(json.dumps(contract(approved), indent=2) + "\n")
    _ = shutil.copyfile(SKILL / GATE, target / GATE)
    _ = shutil.copytree(SKILL / GATE_FIXTURES, target / GATE_FIXTURES)
    _ = shutil.copyfile(SKILL / "evals" / "journeys.json", target / "evals" / "journeys.json")
    out = Path(tempfile.mkdtemp(prefix="skillz-run-"))
    print(f"target: {target}\nout:    {out}\ncontract status: {'approved' if approved else 'draft'}\n")
    print(f"python3 {SKILL / 'scripts' / 'skillz-experiment.pyz'} run --target {target} --out {out} --harness claude "
          + "--model MODEL --judge-model JUDGE_MODEL --repeats 2 --live")
    print("The first call stops with cases-missing. Copy the cases.seed.json it names to OUT/cases.draft.json, then follow the coded stops.")


def main(argv: list[str]) -> None:
    approved = "--approve-contract" in argv
    words = [word for word in argv if word != "--approve-contract"]
    if words[:1] == ["write"] and len(words) == 2:
        write(Path(words[1]), approved)
    elif words == ["write-skill"] and not approved:
        write_skill()
    elif words == ["stage"]:
        stage(approved)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
