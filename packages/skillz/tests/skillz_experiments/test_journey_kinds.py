"""Several journey kinds in one contract, and `run --kind` as the choice of the scored kinds."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest
from test_contract import echo_skill, set_contract
from test_graders import JOURNEY_GATE, FakeJudge, JourneyTask, harness

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import CodedError, load_cases
from skillz_experiments._cli import main
from skillz_experiments._contract import Contract, load_contract
from skillz_experiments._intake import case_hash, load_draft, split_cases
from skillz_experiments.evaluation import CheckMemo
from skillz_experiments.run._plan import scored_kinds, size_plan
from skillz_experiments.harness import ClaudeOptions

ZERO_GATE = "import json\nprint(json.dumps({'score': 0}))"
RUBRIC_A = "RUBRIC_A_SENTINEL: score the review against `reference.must`."
RUBRIC_B = "RUBRIC_B_SENTINEL: score the interview against `reference.must`."


def journey_kind(script: str, rubric: str) -> dict[str, object]:
    return {"grader": "journey", "argv": [sys.executable, "-I", "-c", script], "rubric": rubric}


def two_journey_kinds(target: Path) -> None:
    set_contract(target, kinds={"journey-a": journey_kind(ZERO_GATE, RUBRIC_A),
                                "journey-b": journey_kind(JOURNEY_GATE, RUBRIC_B)})


def task_and_journey(target: Path) -> None:
    set_contract(target, kinds={"task": {"grader": "exact-json"}, "journey": journey_kind(JOURNEY_GATE, RUBRIC_B)})


CRITERIA = [{"id": "must-0", "text": "THE transcript SHALL show a short plan.", "from": "must:0"}]


def journeys(kind: str, prefix: str, families: int = 4, size: int = 2) -> list[dict[str, object]]:
    return [{"id": f"{prefix}{family}-{index}", "family": f"{prefix}{family}", "kind": kind, "request": "Plan the work.",
             "brief": "BRIEF_SENTINEL: wants a short plan.", "expected": {"must": ["EXPECTED_SENTINEL"], "criteria": CRITERIA},
             "source": "skill"} for family in range(families) for index in range(size)]


def tasks(families: int) -> list[dict[str, object]]:
    return [{"id": f"t{family}-{index}", "family": f"t{family}", "kind": "task", "request": "Echo hello",
             "files": {"input.txt": "hello\n"}, "expected": {"echo": "hello"}, "source": "skill"}
            for family in range(families) for index in range(2)]


def walk(tmp_path: Path, target: Path, items: list[dict[str, object]], capsys: pytest.CaptureFixture[str],
         *flags: str) -> tuple[dict[str, object], Path]:
    """Walk `run` to the prepared run. Return the case approval stop data and the run directory."""
    out = tmp_path / "run"
    out.mkdir(mode=0o700)
    _ = (out / "cases.draft.json").write_text(json.dumps(items))
    command = ["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", "prose",
               "--repeats", "1", *flags]
    _ = main(command)
    first = capsys.readouterr()
    assert cast(dict[str, object], json.loads(first.err))["code"] == "cases-unapproved", first.err
    data = cast(dict[str, object], json.loads(first.out))
    command += ["--approve-cases", str(data["case_hash"])]
    _ = main(command)
    second = capsys.readouterr()
    assert cast(dict[str, object], json.loads(second.err))["code"] == "budget-unapproved", second.err
    stop = cast(dict[str, object], json.loads(second.out))
    assert "estimate" in stop, (stop, second.err)
    estimate = cast(dict[str, int], stop["estimate"])
    _ = main([*command, "--approve-budget", str(estimate["calls"])])
    assert cast(dict[str, object], json.loads(capsys.readouterr().err))["code"] == "live-required"
    return data, out


def frozen(out: Path, rules: Contract) -> dict[str, str]:
    return {case.identifier: case.kind for case in load_cases(out / "cases.json", rules.grader_types())}


@pytest.mark.ac("gh-368:AC-7")
def test_a_contract_with_two_journey_kinds_loads_and_a_run_scores_both(tmp_path: Path,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    two_journey_kinds(target)
    rules = load_contract(target)
    assert rules.journey_kinds() == ("journey-a", "journey-b")
    assert scored_kinds(rules) == ("journey-a", "journey-b")
    data, out = walk(tmp_path, target, [*journeys("journey-a", "a"), *journeys("journey-b", "b")], capsys)
    assert "| not scored |" not in str(data["question"])
    assert set(frozen(out, rules).values()) == {"journey-a", "journey-b"} and len(frozen(out, rules)) == 16
    plan = size_plan(rules, load_cases(out / "cases.json", rules.grader_types()), 2, ClaudeOptions())
    assert plan.sized.calls > 0


@pytest.mark.ac("gh-368:AC-7")
def test_each_journey_case_is_graded_by_its_own_kinds_gate_and_rubric(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    target = echo_skill(tmp_path)
    two_journey_kinds(target)
    rules = load_contract(target)
    items = [item | {"split": "train", "provenance": "t", "provider_approved": True}
             for item in (journeys("journey-a", "a", 1)[0], journeys("journey-b", "b", 1)[0])]
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "cases": items}))
    by_kind = {case.kind: case for case in load_cases(path, rules.grader_types())}
    candidate = Candidate({"SKILL.md": "seed"}, ("SKILL.md",), contract=rules)
    judge = FakeJudge(3)
    runner = harness(monkeypatch, JourneyTask(), judge)
    assert runner.evaluate(candidate, by_kind["journey-a"], checks=CheckMemo())["score"] == 0
    assert judge.prompts == []
    assert runner.evaluate(candidate, by_kind["journey-b"], checks=CheckMemo())["score"] == pytest.approx(2 / 3)
    assert len(judge.prompts) == 1 and RUBRIC_B in judge.prompts[0] and RUBRIC_A not in judge.prompts[0]


@pytest.mark.ac("gh-368:AC-8")
def test_kind_journey_scores_only_the_journey_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    task_and_journey(target)
    rules = load_contract(target)
    data, out = walk(tmp_path, target, [*tasks(2), *journeys("journey", "j", size=3)], capsys, "--kind", "journey")
    question = str(data["question"]).splitlines()
    assert sum("| not scored |" in line for line in question if line.startswith("- t")) == 4
    assert sum("| scored |" in line for line in question if line.startswith("- j")) == 12
    document = cast(dict[str, list[dict[str, object]]], json.loads((out / "cases.json").read_text()))
    assert {item["kind"] for item in document["cases"]} == {"journey"} and len(document["cases"]) == 12
    assert len(document["not_scored"]) == 4
    assert set(frozen(out, rules).values()) == {"journey"}


@pytest.mark.ac("gh-368:AC-8")
def test_without_kind_a_declared_task_drives_the_split_and_journeys_are_still_scored(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    task_and_journey(target)
    rules = load_contract(target)
    assert scored_kinds(rules) == ("task",)
    data, out = walk(tmp_path, target, [*tasks(5), *journeys("journey", "j")], capsys)
    assert "| not scored |" not in str(data["question"])
    assert sorted(set(frozen(out, rules).values())) == ["journey", "task"] and len(frozen(out, rules)) == 18


# judge-criteria-verdicts: the journey items gained expected.criteria, which the case hash covers, so the pin moved.
BASE_HASH = "51d780c46ed9a9e5944b82a1d4b290ea62e896f24dd1813ec6ea2abe7ff132d4"
BASE_SPLITS = {"j0": "train", "j1": "holdout", "j2": "train", "t0": "validation", "t1": "holdout",
               "t2": "holdout", "t3": "holdout", "t4": "train"}


@pytest.mark.ac("gh-368:AC-8")
def test_without_kind_the_mixed_draft_keeps_the_base_hash_and_splits(tmp_path: Path) -> None:
    """The hash and splits come from commit b93a7ec, before journey kinds existed."""
    items = [item | {"files": {"a.txt": "x\n"}} if item["kind"] == "journey" else item
             for item in [*tasks(5), *journeys("journey", "j", 3)]]
    path = tmp_path / "cases.draft.json"
    _ = path.write_text(json.dumps(items))
    cases = split_cases(load_draft(path), 7, "task", also=("journey",))
    assert case_hash(cases, 7) == BASE_HASH
    assert {case.family: case.split for case in cases} == BASE_SPLITS
    assert all(case.scored and "scored" not in case.data() for case in cases)


@pytest.mark.ac("gh-368:AC-8")
def test_without_kind_journey_families_do_not_pad_the_task_minimum(tmp_path: Path) -> None:
    items = [*tasks(2), *journeys("journey", "j", 3)]
    path = tmp_path / "cases.draft.json"
    _ = path.write_text(json.dumps(items))
    with pytest.raises(ValueError, match="scored families"):
        _ = split_cases(load_draft(path), 7, "task", also=("journey",))

@pytest.mark.ac("gh-368:AC-8")
def test_task_families_do_not_fill_the_holdout_of_a_journey_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = echo_skill(tmp_path)
    task_and_journey(target)
    items = [*tasks(5), *journeys("journey", "j", 2)]
    out = tmp_path / "run"
    out.mkdir(mode=0o700)
    _ = (out / "cases.draft.json").write_text(json.dumps(items))
    _ = main(["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", "prose",
              "--kind", "journey"])
    error = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert "scored families" in str(error["error"]) and error["code"] == "cases-too-few" and "has 2" in str(error["error"])


def test_the_default_kinds_follow_the_contract_and_a_bad_selection_stops(tmp_path: Path) -> None:
    target = echo_skill(tmp_path)
    task_and_journey(target)
    rules = load_contract(target)
    assert scored_kinds(rules, ["journey", "task"]) == ("journey", "task")
    with pytest.raises(CodedError, match="names no declared kind") as undeclared:
        _ = scored_kinds(rules, ["nope"])
    assert undeclared.value.code == "contract-kinds"
    set_contract(target, kinds={"echo": {"grader": "exact-json"}, "rewrite": {"grader": "exact-json"}})
    mixed = load_contract(target)
    with pytest.raises(CodedError, match="--kind") as several:
        _ = scored_kinds(mixed)
    assert several.value.code == "contract-kinds" and scored_kinds(mixed, ["echo"]) == ("echo",)
    with pytest.raises(CodedError, match="at most one kind") as two_plain:
        _ = scored_kinds(mixed, ["echo", "rewrite"])
    assert two_plain.value.code == "contract-kinds"
    set_contract(target, kinds={"echo": {"grader": "audit"}})
    with pytest.raises(CodedError) as audit:
        _ = scored_kinds(load_contract(target))
    assert audit.value.code == "contract-audit-unsupported"


@pytest.mark.ac("gh-368:AC-7")
def test_a_journey_labeled_case_stops_in_a_contract_without_that_kind(tmp_path: Path) -> None:
    item = {"id": "j-0", "family": "j", "kind": "journey", "request": "Plan the work.", "source": "skill",
            "expected": {"must": ["x"], "criteria": CRITERIA}}
    path = tmp_path / "cases.draft.json"
    _ = path.write_text(json.dumps([item]))
    with pytest.raises(ValueError, match="case j-0: kind must be one of trigger, near-miss, task, journey-review"):
        _ = load_draft(path, ("journey-review",))
    with pytest.raises(ValueError, match="case j-0: kind must be one of trigger, near-miss, task$"):
        _ = load_draft(path, ())
