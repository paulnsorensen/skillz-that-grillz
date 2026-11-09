"""gh-694: `validate` reports each judged case that `run` refuses for its criteria."""
from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main

FIXTURE = Path(__file__).parent / "fixtures/culture-journey"
CRITERIA = [{"id": "must-0", "text": "THE transcript SHALL show that the agent asks a question.", "from": "must:0"}]


def _journey_skill(tmp_path: Path, criteria: Sequence[Mapping[str, object]] | None = None) -> Path:
    """Copy the culture journey skill with 13 journey cases in 5 families. `criteria` goes on every case when given."""
    target = tmp_path / "culture"
    _ = shutil.copytree(FIXTURE, target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    _ = (target / "evals/gates/journey.py.fixture").rename(target / "evals/gates/journey.py")
    expected: dict[str, object] = {"must": ["asks a question"], "must_not": ["writes source code"]}
    if criteria is not None:
        expected["criteria"] = criteria
    cases = [{"id": f"culture-{family}-{index}", "family": f"f{family}", "kind": "journey",
              "request": f"/culture case {family}.{index}", "brief": "You lead a small team. Answer briefly.",
              "max_turns": 3, "expected": expected, "source": "skill"}
             for family in range(5) for index in range(3 if family < 3 else 2)]
    _ = (target / "evals/journeys.json").write_text(json.dumps(cases))
    return target


def _codes(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, list[dict[str, object]]]:
    """Return the exit code and the criteria findings; the copied gate adds its own warnings."""
    code = main(["validate", *arguments])
    findings = cast(list[dict[str, object]], json.loads(capsys.readouterr().out)["findings"])
    return code, [item for item in findings if str(item["code"]).startswith("case-criteria")]


def _draft_from_seed(target: Path, out: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """Stop before model execution and copy the seed into the draft."""
    assert main(["run", "--target", str(target), "--out", str(out), "--model", "m", "--harness", "claude"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "cases-missing"
    draft = out / "cases.draft.json"
    _ = draft.write_bytes((out / "cases.seed.json").read_bytes())
    return draft


@pytest.mark.ac("gh-694:AC-1")
@pytest.mark.parametrize("wrapped", [False, True])
def test_validate_reports_each_draft_journey_case_without_criteria_that_run_refuses(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], wrapped: bool) -> None:
    """Locate every missing criteria field in either supported draft shape."""
    target, out = _journey_skill(tmp_path), tmp_path / "run"
    draft = _draft_from_seed(target, out, capsys)
    document = cast(dict[str, object], json.loads(draft.read_text()))
    _ = draft.write_text("\n  " + json.dumps(document if wrapped else document["cases"]))
    code, findings = _codes(capsys, "--target", str(target), "--draft", str(draft), "--kind", "journey")
    missing = [item for item in findings if item["code"] == "case-criteria-missing"]
    assert code == 1 and len(missing) == len(findings) == 13, findings
    assert all(item["severity"] == "error" and item["file"] == str(draft) and item["fix"] for item in missing)
    prefix = "/cases" if wrapped else ""
    assert [item["pointer"] for item in missing] == [f"{prefix}/{index}/expected/criteria" for index in range(13)]
    assert "culture-0-0" in str(missing[0]["message"])
    assert main(["run", "--target", str(target), "--out", str(out), "--model", "m", "--harness", "claude"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == "case-criteria-missing"


@pytest.mark.ac("gh-694:AC-2")
def test_validate_leaves_missing_seed_criteria_to_the_draft_and_accepts_a_draft_with_criteria(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Accept seeds without criteria and drafts with valid criteria."""
    code, findings = _codes(capsys, "--target", str(_journey_skill(tmp_path / "missing")), "--kind", "journey")
    assert code == 0 and findings == []
    target = _journey_skill(tmp_path / "good", CRITERIA)
    draft = _draft_from_seed(target, tmp_path / "run", capsys)
    code, findings = _codes(capsys, "--target", str(target), "--draft", str(draft), "--kind", "journey")
    assert code == 0 and findings == []


@pytest.mark.ac("gh-694:AC-1")
@pytest.mark.parametrize("wrapped", [False, True])
def test_validate_reports_a_judged_draft_task_case_whose_criteria_are_not_ears(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], wrapped: bool) -> None:
    """Locate invalid criteria without reporting valid cases between them."""
    target = _journey_skill(tmp_path)
    contract = cast(dict[str, object], json.loads((target / "evals/autoimprove.json").read_text()))
    contract["kinds"] = {"task": {"grader": "judge", "rubric": "Grade each criterion."}}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract))
    (target / "evals/journeys.json").unlink()
    criteria = [{"id": "answer", "text": "answers"}]
    cases = [{"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task", "request": f"Read input.txt {family}.{index}",
              "files": {"input.txt": "hello\n"}, "expected": {"answer": "hi", "criteria": criteria}, "source": "skill"}
             for family in range(5) for index in range(3)]
    draft = tmp_path / "cases.draft.json"
    cases[1]["expected"] = {"answer": "hi", "criteria": CRITERIA}
    _ = draft.write_text(json.dumps({"cases": cases} if wrapped else cases))
    code, findings = _codes(capsys, "--target", str(target), "--draft", str(draft))
    assert code == 1 and [item["code"] for item in findings] == ["case-criteria-invalid"] * 14, findings
    prefix = "/cases" if wrapped else ""
    assert [item["pointer"] for item in findings] == [f"{prefix}/{index}/expected/criteria" for index in range(15) if index != 1]
    assert "t0-0" in str(findings[0]["message"]) and "answer" in str(findings[0]["message"])
