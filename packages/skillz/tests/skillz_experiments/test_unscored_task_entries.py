"""gh-701: a run that does not score the task kind needs no family and no files on an `evals.json` task entry."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main

FIXTURE = Path(__file__).parent / "fixtures/culture-journey"
CRITERIA = [{"id": "must-0", "text": "THE transcript SHALL show that the agent asks a question.", "from": "must:0"}]


def _target(tmp_path: Path, entry: dict[str, object]) -> Path:
    """Copy the journey-only culture skill with 15 journey cases in 5 families and one task entry."""
    target = tmp_path / "culture"
    _ = shutil.copytree(FIXTURE, target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    _ = (target / "evals/gates/journey.py.fixture").rename(target / "evals/gates/journey.py")
    expected: dict[str, object] = {"must": ["asks a question"], "must_not": ["writes source code"], "criteria": CRITERIA}
    cases = [{"id": f"culture-{family}-{index}", "family": f"f{family}", "kind": "journey",
              "request": f"/culture case {family}.{index}", "brief": "You lead a small team. Answer briefly.",
              "max_turns": 3, "expected": expected, "source": "skill"}
             for family in range(5) for index in range(3)]
    _ = (target / "evals/journeys.json").write_text(json.dumps(cases))
    (target / "evals/files").mkdir()
    _ = (target / "evals/files/input.txt").write_text("hello\n")
    _ = (target / "evals/evals.json").write_text(json.dumps({"evals": [entry]}))
    return target


def _greet(**extra: object) -> dict[str, object]:
    return {"name": "greet", "prompt": "Greet the user.", "expected_output": "Says hello.",
            "assertions": ["says hello"], **extra}


def _run(target: Path, out: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, dict[str, object]]:
    code = main(["run", "--target", str(target), "--out", str(out), "--model", "m", "--harness", "claude",
                 "--kind", "journey", *extra])
    return code, cast(dict[str, object], json.loads(capsys.readouterr().err))


@pytest.mark.ac("gh-701:AC-1")
@pytest.mark.parametrize("entry", [_greet(files=["evals/files/input.txt"]), _greet()], ids=["with-files", "fileless"])
def test_validate_accepts_an_unscored_task_entry_without_a_family(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], entry: dict[str, object]) -> None:
    target = _target(tmp_path, entry)
    assert main(["validate", "--target", str(target), "--kind", "journey"]) == 0
    report = cast(dict[str, object], json.loads(capsys.readouterr().out))
    codes = {str(item["code"]) for item in cast(list[dict[str, object]], report["findings"])}
    assert not codes & {"case-family-missing", "cases-invalid"}, codes


@pytest.mark.ac("gh-701:AC-2")
@pytest.mark.parametrize("entry", [_greet(files=["evals/files/input.txt"]), _greet()], ids=["with-files", "fileless"])
def test_journey_run_copies_the_seed_with_an_unscored_task_entry_to_the_approval_stop(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], entry: dict[str, object]) -> None:
    target, out = _target(tmp_path, entry), tmp_path / "run"
    assert _run(target, out, capsys)[1]["code"] == "cases-missing"
    _ = (out / "cases.draft.json").write_bytes((out / "cases.seed.json").read_bytes())
    code, error = _run(target, out, capsys)
    assert code == 1 and error["code"] == "cases-unapproved", error


@pytest.mark.ac("gh-701:AC-3")
def test_a_run_that_scores_the_task_kind_still_requires_the_family_of_a_task_case(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target, out = _target(tmp_path, _greet(files=["evals/files/input.txt"])), tmp_path / "run"
    contract = cast(dict[str, object], json.loads((target / "evals/autoimprove.json").read_text()))
    contract["kinds"] = {"task": {"grader": "judge", "rubric": "Grade each criterion."}}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))
    _ = (target / "evals/journeys.json").unlink()
    draft = [{"id": "greet", "kind": "task", "request": "Greet.", "source": "skill", "files": {"input.txt": "hello\n"},
              "expected": "Says hello."}]
    out.mkdir(mode=0o700)
    _ = (out / "cases.draft.json").write_text(json.dumps(draft))
    code = main(["run", "--target", str(target), "--out", str(out), "--model", "m", "--harness", "claude"])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert code == 1 and error["code"] == "cases-invalid" and "missing fields family" in str(error["error"]), error
