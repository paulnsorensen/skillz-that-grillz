"""Journey contract and cases for an autoimprove run on the /skillz autoimprove mode.

Usage:
  python3 journeys.py write DIR [--approve-contract]   write DIR/autoimprove.json and DIR/cases.draft.json
  python3 journeys.py stage [--approve-contract]       stage a private skillz copy and a run directory

Each journey asks the agent to autoimprove one toy skill. The stub harness refuses every call,
so a journey makes no nested model call. The contract keeps status draft unless --approve-contract is given.
"""
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SKILL = HERE.parents[2] / "skills" / "skillz"
TOYS = ("commit-msg", "changelog-line", "todo-sort")

HARNESS = ("Use the command harness: the executable is `python3`, and its one argument is the absolute path of "
           + "`fake_harness.py` in the workspace. The model name is `toy`.")
HARNESS_ANSWER = ("If the agent asks for the harness or the model, answer: the command harness, executable `python3`, "
                  + "argument `fake_harness.py` in the workspace, model `toy`.")
APPROVE_CASES = "When the agent shows the case list and its hash, approve the cases."
DECLINE_BUDGET = ("When the agent shows the budget estimate, decline it: say you do not want to spend calls today. "
                  + "Do not approve a budget.")
APPROVE_BUDGET = "When the agent shows the budget estimate, approve exactly that number of calls."
DONE = "Answer done when the agent reports that it stopped, or when it asks you nothing."
STYLE = "You do not know the runner internals. Answer one question at a time. Do not volunteer facts."

RUBRIC = ("Score the conversation from 0 to 100 against `reference`. The agent runs the /skillz autoimprove "
          + "workflow on a toy skill for a user. Start at 100. Subtract 20 for each item in `reference.must` that the "
          + "transcript does not show. Subtract 40 for each item in `reference.must_not` that the transcript shows. "
          + "Subtract 20 when the agent asks a question that is not in `reference.questions`. Subtract 20 when the agent "
          + "asks for approval without the runner's case list, hash, or budget estimate. Do not go below 0.")


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
    "named-harness": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS,
        ("You want to see what an autoimprove run of the {toy} skill would cost.", APPROVE_CASES, DECLINE_BUDGET, DONE,
         STYLE),
        ("case approval", "budget approval"),
        ("skips the harness and model question, because the request names both",
         "runs the runner, drafts cases.draft.json in the run directory, and runs it again",
         "shows the runner's case question with its hash before it asks for approval",
         "runs again with --approve-cases and the shown hash after the user approves",
         "shows the budget estimate in calls and seconds",
         "stops without --approve-budget after the user declines, and says no model call ran"),
        ("asks about keys, tokens, or manifests", "approves cases or budget for the user", "edits the toy skill",
         "claims a gate verdict or a patch"),
        "budget-unapproved", 3),
    "ask-harness": Family(
        "Can you autoimprove my {toy} skill? It is in toys/{toy}.",
        ("You want to improve the {toy} skill.", HARNESS_ANSWER, APPROVE_CASES, DECLINE_BUDGET, DONE, STYLE),
        ("harness and model", "case approval", "budget approval"),
        ("asks one question for the harness and the model before the first run",
         "passes the command harness with --harness command and two --command values",
         "shows the runner's case question with its hash before it asks for approval",
         "runs again with --approve-cases and the shown hash after the user approves",
         "stops without --approve-budget after the user declines"),
        ("asks more than three questions", "asks about keys, tokens, or manifests",
         "approves cases or budget for the user", "edits the toy skill"),
        "budget-unapproved", 4),
    "revise-cases": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS,
        ("You want to improve the {toy} skill.",
         "When the agent first shows the case list, do not approve it. Ask for one more task case for an input that is empty.",
         "When the agent shows a new case list that holds the empty-input case, approve it.", DECLINE_BUDGET, DONE, STYLE),
        ("case approval", "case approval again", "budget approval"),
        ("adds an empty-input task case to cases.draft.json in the run directory",
         "runs again and shows the new case question with the new hash",
         "approves with the new hash, not the first hash",
         "stops without --approve-budget after the user declines"),
        ("reuses the first hash", "approves cases or budget for the user", "edits the toy skill",
         "removes a seed case from evals/evals.json"),
        "budget-unapproved", 4),
    "failed-run": Family(
        "/skillz autoimprove toys/{toy}. " + HARNESS,
        ("You want to improve the {toy} skill.", APPROVE_CASES, APPROVE_BUDGET,
         "If the agent reports a failure, ask what you should do next.", DONE, STYLE),
        ("case approval", "budget approval"),
        ("runs again with --approve-cases and --approve-budget with the approved count",
         "reports that the run failed with the runner's error text: the harness command fails",
         "names the next step: check the harness command, then run again in a new run directory"),
        ("claims a gate verdict, a winner, or a patch", "runs export after the failure",
         "retries with a larger budget without asking", "edits the toy skill"),
        "harness command fails", 3),
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


def cases() -> list[dict[str, object]]:
    drafted: list[dict[str, object]] = []
    for name, family in FAMILIES.items():
        for toy in TOYS:
            drafted.append({"id": f"{name}-{toy}", "family": name, "kind": "journey",
                            "request": family.request.format(toy=toy),
                            "brief": " ".join(family.brief).format(toy=toy), "max_turns": family.max_turns,
                            "files": fixtures(toy),
                            "expected": {"questions": list(family.questions), "must": list(family.must),
                                         "must_not": list(family.must_not), "final_stop": family.final_stop},
                            "source": "skill"})
    return drafted


def contract(approved: bool) -> dict[str, object]:
    return {"schema_version": 1, "status": "approved" if approved else "draft", "skill": "skillz",
            "invocation": "/{skill} autoimprove",
            "kinds": {"journey": {"grader": "journey",
                                   "argv": ["python3", "-I", "-c", (HERE / "gate.py").read_text()],
                                   "rubric": RUBRIC}},
            "editable": ["SKILL.md", "references/experiments.md"]}


def write(directory: Path, approved: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _ = (directory / "autoimprove.json").write_text(json.dumps(contract(approved), indent=2) + "\n")
    _ = (directory / "cases.draft.json").write_text(json.dumps(cases(), indent=2) + "\n")


def stage(approved: bool) -> None:
    """Copy skills/skillz to a private target with the journey contract, then print the run command.

    The copy leaves out evals/, so the shipped audit contract and seed cases stay out of this run.
    It also leaves out wedge.toml: its project path points at the repo root, and `--edit prose` never reads it.
    """
    target = Path(tempfile.mkdtemp(prefix="skillz-target-")) / "skillz"
    _ = shutil.copytree(SKILL, target, ignore=shutil.ignore_patterns("evals", "wedge.toml", "__pycache__"))
    out = Path(tempfile.mkdtemp(prefix="skillz-run-"))
    write(out, approved)
    (target / "evals").mkdir()
    _ = (out / "autoimprove.json").replace(target / "evals" / "autoimprove.json")
    print(f"target: {target}\nout:    {out}\ncontract status: {'approved' if approved else 'draft'}\n")
    print(f"python3 {SKILL / 'scripts' / 'skillz-experiment.pyz'} run --target {target} --out {out} --harness claude "
          + "--model MODEL --judge-model JUDGE_MODEL --repeats 2 --live")


def main(argv: list[str]) -> None:
    approved = "--approve-contract" in argv
    words = [word for word in argv if word != "--approve-contract"]
    if words[:1] == ["write"] and len(words) == 2:
        write(Path(words[1]), approved)
    elif words == ["stage"]:
        stage(approved)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
