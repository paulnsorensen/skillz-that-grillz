"""The typed journey `expected` object, its field table, `requirements`, and journey cases without files."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from test_graders import JOURNEY_BRIEF, JOURNEY_GATE, FakeJudge, JourneyTask, contract as echo_contract, harness, journey_contract

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import JOURNEY_EXPECTED_FIELDS, Case, CodedError, load_cases
from skillz_experiments._contract import load_contract
from skillz_experiments._graders import judge as judge_case
from skillz_experiments._intake import DRAFT_NAME, case_hash, freeze, load_draft, split_cases
from skillz_experiments._scaffold import NEXT, RUBRIC, scaffold
from skillz_experiments.evaluation import CheckMemo

KIT = Path(__file__).parents[2] / "journey-kits/skillz-autoimprove/journeys.py"
EASY_CHEESE: dict[str, object] = {
    "id": "age-1", "family": "age", "kind": "journey", "request": "/plate the change",
    "brief": "You want a PR. Answer briefly.", "max_turns": 6, "requirements": ["AGE-1", "AGE-2"],
    "files": {"src/a.py": "x = 1\n"},
    "expected": {"must": ["opens a PR"], "must_not": ["force-pushes"], "criteria": [
        {"id": "must-0", "text": "THE transcript SHALL show that the agent opens a PR.", "from": "must:0"}]},
    "source": "skill",
}
CRITERIA = [{"id": "c-0", "text": "WHEN the user asks, THE agent SHALL answer."}]
# judge-criteria-verdicts: the kit cases gained expected.criteria, which the case hash covers, so the pin moved.
KIT_HASH = "ec109da9f860807577623e9549f4d86205dcac6fe6ba885057d196427a9af9a5"
TASK_HASH = "37dffac39b162cbff4a6e2ecb98ac7d355bff74a38856c080b1d2763047488b8"


def write_draft(tmp_path: Path, items: list[dict[str, object]]) -> Path:
    path = tmp_path / DRAFT_NAME
    _ = path.write_text(json.dumps(items), encoding="utf-8")
    return path


def one(**changes: object) -> list[dict[str, object]]:
    return [EASY_CHEESE | changes]


def without(key: str) -> list[dict[str, object]]:
    return [{name: value for name, value in EASY_CHEESE.items() if name != key}]


def load_one(tmp_path: Path, **changes: object) -> list[Case]:
    item = EASY_CHEESE | changes | {"split": "train", "provenance": "t", "provider_approved": True}
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": [item]}), encoding="utf-8")
    return load_cases(path, {"journey": "journey"})


@pytest.mark.ac("gh-368:AC-1")
def test_an_unknown_expected_key_stops_the_draft_and_the_message_names_it(tmp_path: Path) -> None:
    path = write_draft(tmp_path, one(expected={"forks": ["x"]}))
    with pytest.raises(CodedError) as caught:
        _ = load_draft(path)
    assert caught.value.code == "journey-expected-invalid"
    assert "forks" in str(caught.value) and "must_not" in str(caught.value)


@pytest.mark.ac("gh-368:AC-2")
@pytest.mark.parametrize("expected", [
    {"must": "x"}, {"must": [], "must_not": []}, {"questions": ["q"]}, {"must": ["a", "a"]}, {"must": [" "]},
    {"must": [1]}, {"must": ["a"], "questions": "q"}, {"must": ["a"], "final_stop": " "},
    {"must": ["a"], "final_stop": 3}, ["must"], "must",
], ids=["text", "both-empty", "no-must", "duplicate", "blank", "int", "questions-text", "stop-blank", "stop-int", "list",
         "string"])
def test_a_malformed_expected_object_stops_the_draft(tmp_path: Path, expected: object) -> None:
    with pytest.raises(CodedError) as caught:
        _ = load_draft(write_draft(tmp_path, one(expected=expected)))
    assert caught.value.code == "journey-expected-invalid"


@pytest.mark.ac("gh-368:AC-2")
def test_a_journey_without_expected_stops_with_expected_missing(tmp_path: Path) -> None:
    with pytest.raises(CodedError) as caught:
        _ = load_draft(write_draft(tmp_path, without("expected")))
    assert caught.value.code == "expected-missing"


@pytest.mark.ac("gh-368:AC-2")
@pytest.mark.parametrize("expected", [
    {"must_not": ["x"], "criteria": CRITERIA}, {"must": ["x"], "questions": [], "criteria": CRITERIA},
    {"must": ["x"], "final_stop": "stops", "questions": ["q"], "criteria": CRITERIA},
])
def test_a_conforming_expected_object_keeps_its_raw_form(tmp_path: Path, expected: dict[str, object]) -> None:
    cases = load_one(tmp_path, expected=expected)
    assert cases[0].expected == expected
    assert load_draft(write_draft(tmp_path, one(expected=expected)))[0].expected == expected


@pytest.mark.ac("gh-368:AC-3")
def test_load_cases_rejects_a_misspelled_expected_key(tmp_path: Path) -> None:
    with pytest.raises(CodedError) as caught:
        _ = load_one(tmp_path, expected={"must_nto": ["x"]})
    assert caught.value.code == "journey-expected-invalid" and "must_nto" in str(caught.value)
    assert "new run" in str(caught.value)


@pytest.mark.ac("gh-368:AC-4")
def test_the_journey_judge_prompt_defines_every_expected_field_and_the_judge_kind_does_not(tmp_path: Path) -> None:
    rules = journey_contract(JOURNEY_GATE)
    case = load_one(tmp_path)[0]
    judge = FakeJudge(4)
    _ = judge_case(judge, rules.grader("journey"), case, {}, None, holdout=False)
    prompt = judge.prompts[0].split("UNTRUSTED DATA (JSON):\n", 1)[0]
    assert all(f"reference.{name}" in prompt and meaning in prompt for name, meaning in JOURNEY_EXPECTED_FIELDS.items())
    plain = FakeJudge(4)
    _ = judge_case(plain, echo_contract().grader("style"), case, {}, None, holdout=False)
    assert not any(f"reference.{name}" in plain.prompts[0] for name in JOURNEY_EXPECTED_FIELDS)


def names(rubric: str) -> set[str]:
    return set(re.findall(r"reference\.([a-z_]+)", rubric))


def kit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("kit_journeys", KIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.ac("gh-368:AC-5")
def test_the_scaffold_and_kit_rubrics_name_every_field_and_the_next_step_lists_them() -> None:
    fields = set(JOURNEY_EXPECTED_FIELDS)
    for rubric in (RUBRIC, cast(str, kit().RUBRIC)):
        assert "criteria" in names(rubric) and names(rubric) <= fields
    assert all(f"expected.{name}" in NEXT for name in fields)


@pytest.mark.ac("gh-368:AC-5")
def test_a_scaffolded_contract_loads_and_a_rubric_with_an_unknown_reference_field_stops(tmp_path: Path) -> None:
    skill = tmp_path / "skill"
    skill.mkdir()
    _ = (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n", encoding="utf-8")
    path = Path(cast(str, scaffold(skill)["contract"]))
    written = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    written["status"] = "approved"
    _ = path.write_text(json.dumps(written), encoding="utf-8")
    _ = load_contract(skill)
    journey = cast(dict[str, dict[str, str]], written["kinds"])["journey"]
    journey["rubric"] += " Subtract 5 for each `reference.forks` the transcript lacks."
    _ = path.write_text(json.dumps(written), encoding="utf-8")
    with pytest.raises(CodedError) as caught:
        _ = load_contract(skill)
    assert caught.value.code == "contract-rubric-field" and "forks" in str(caught.value)


@pytest.mark.ac("gh-368:AC-6")
@pytest.mark.parametrize("requirements", [["R1", "R1"], ["R1", ""], ["R1", " "], "R1"])
def test_requirements_with_a_duplicate_or_empty_entry_are_rejected(tmp_path: Path, requirements: object) -> None:
    with pytest.raises(ValueError):
        _ = load_draft(write_draft(tmp_path, one(requirements=requirements)))
    with pytest.raises(ValueError):
        _ = load_one(tmp_path, requirements=requirements)


@pytest.mark.ac("gh-368:AC-6")
def test_an_easy_cheese_shaped_case_passes_intake_unchanged(tmp_path: Path) -> None:
    items = [EASY_CHEESE | {"id": f"age-{n}", "family": f"age{n}"} for n in range(9)]
    cases = split_cases(load_draft(write_draft(tmp_path, items)), 5, None, ["journey"])
    assert len(cases) == 9
    for case in cases:
        assert case.data() == EASY_CHEESE | {"id": case.identifier, "family": case.family, "split": case.split}


def task(index: int) -> dict[str, object]:
    return {"id": f"t{index}", "family": f"f{index % 5}", "kind": "task", "request": "Do it",
            "files": {"input.txt": "x\n"}, "expected": {"answer": "y"}, "source": "skill"}


@pytest.mark.ac("gh-368:AC-9")
def test_a_journey_without_files_passes_intake_freezes_counts_toward_the_holdout_and_is_eligible(tmp_path: Path) -> None:
    items = [{name: value for name, value in EASY_CHEESE.items() if name != "files"}
             | {"id": f"j{n}", "family": f"jf{n % 6}"} for n in range(18)]
    cases = split_cases(load_draft(write_draft(tmp_path, items)), 4, None, ["journey"])
    assert sum(case.split == "holdout" for case in cases) >= 6
    assert all("files" not in case.data() for case in cases)
    out = tmp_path / "cases.json"
    freeze(out, cases, 4, case_hash(cases, 4))
    loaded = load_cases(out, {"journey": "journey"})
    assert len(loaded) == 18 and all(case.files == {} and case.eligible for case in loaded)


@pytest.mark.ac("gh-368:AC-9")
def test_a_journey_case_without_files_is_evaluated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rules = journey_contract(JOURNEY_GATE)
    case = load_one(tmp_path, brief=JOURNEY_BRIEF, files={})[0]
    candidate = Candidate({"SKILL.md": "seed"}, ("SKILL.md",), contract=rules)
    result = harness(monkeypatch, JourneyTask(), FakeJudge(3)).evaluate(candidate, case, checks=CheckMemo())
    assert result["score"] == pytest.approx(2 / 3)


@pytest.mark.ac("gh-368:AC-9")
def test_a_task_case_without_files_is_still_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="needs files"):
        _ = load_draft(write_draft(tmp_path, [task(0) | {"files": {}}]))
    with pytest.raises(ValueError, match="needs files"):
        _ = load_draft(write_draft(tmp_path, [without_files(task(0))]))


def without_files(item: dict[str, object]) -> dict[str, object]:
    return {name: value for name, value in item.items() if name != "files"}


@pytest.mark.ac("gh-368:AC-10")
def test_the_kit_cases_and_a_task_run_keep_their_case_hash(tmp_path: Path) -> None:
    cast(Callable[[Path, bool], None], getattr(kit(), "write"))(tmp_path / "kit", False)
    kit_cases = split_cases(load_draft(tmp_path / "kit/cases.draft.json"), 7, None, ["journey"])
    assert case_hash(kit_cases, 7) == KIT_HASH
    task_cases = split_cases(load_draft(write_draft(tmp_path, [task(index) for index in range(20)])), 3)
    assert case_hash(task_cases, 3) == TASK_HASH
