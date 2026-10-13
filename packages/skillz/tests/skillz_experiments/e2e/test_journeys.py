"""Joined Claude journey search, holdout, and export tests."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from support import (CLI, FIXTURES, Scenario, call_cli, command_adapter, export_and_apply, make_scenario,
                     make_vendor_bin, read_cases, read_events, read_record, run_scenario, snapshot)

BUNDLE = Path(__file__).resolve().parents[5] / "skills/skillz/scripts/skillz-experiment.pyz"


def journey_scenario(base: Path, *, helper: bool = False, mixed: bool = False,
                     failed_gate: bool = False) -> Scenario:
    scenario = make_scenario(base, helper=helper)
    contract_path = scenario.target / "evals/autoimprove.json"
    contract = cast(dict[str, object], json.loads(contract_path.read_text()))
    kinds = cast(dict[str, object], contract["kinds"])
    kinds["journey"] = {
        "grader": "journey",
        "argv": ["/usr/bin/python3", "-I", str(FIXTURES / "journey.py"), "fail" if failed_gate else "grade"],
        "rubric": "Score the actual result file and conversation against the reference."
    }
    if not mixed:
        del kinds["task"]
    _ = contract_path.write_text(json.dumps(contract))
    draft: list[dict[str, object]] = []
    for index in range(10):
        label = f"maple-{index}"
        topic = "label" if index % 2 else "code"
        draft.append({
            "id": f"j{index}", "family": f"jfamily{index // 2}", "kind": "journey",
            "request": "Ask which value to transform, then write output/result.txt.",
            "brief": f"Value: {label}. Answer only the agent's question.",
            "max_turns": 2,
            "files": {"fixture.md": "seed\n", "question.txt": f"{topic}\n"},
            "expected": {"text": label.upper() + "\n"},
            "source": "skill",
        })
    if mixed:
        draft = [item | {"family": f"f{index}"} for index, item in enumerate(draft[:5])]
        for family in range(5):
            for index in range(3):
                source = f"case-{family}-{index}\n"
                draft.append({"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task",
                              "request": "Transform fixture.md according to this skill.",
                              "files": {"fixture.md": source}, "expected": {"text": source.upper()},
                              "source": "skill"})
    return Scenario(scenario.target, scenario.out, scenario.export, draft, scenario.original)


def _record(scenario: Scenario) -> dict[str, object]:
    return read_record(scenario.out)


def _holdout(record: dict[str, object]) -> list[dict[str, object]]:
    return [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])
            if cast(dict[str, object], item).get("split") == "holdout"]


def _assert_gate(scenario: Scenario, record: dict[str, object], kinds: set[str]) -> None:
    gate = cast(dict[str, object], record["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"]) == ("promote", 1.0, 0.0)
    assert gate["repeats"] == 3
    held = {cast(str, case["id"]): cast(str, case["kind"]) for case in read_cases(scenario.out) if case["split"] == "holdout"}
    assert set(held.values()) == kinds
    outcomes = _holdout(record)
    assert gate["cases"] == len(held)
    assert len(outcomes) == len(held) * 6
    for name in held:
        for arm, score in (("baseline", 0.0), ("winner", 1.0)):
            selected = [item for item in outcomes if item["case_id"] == name and item["arm"] == arm]
            assert sorted(cast(int, item["repeat"]) for item in selected) == [0, 1, 2]
            assert {item["score"] for item in selected} == {score}


@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
@pytest.mark.parametrize("cli", [CLI, BUNDLE], ids=["source", "bundle"])
@pytest.mark.parametrize("helper", [False, True], ids=["J0", "J1"])
def test_claude_journey_promotes_from_actual_conversation_and_export(
        tmp_path: Path, cli: Path, helper: bool) -> None:
    scenario = journey_scenario(tmp_path, helper=helper)
    before = snapshot(scenario.target)
    result = run_scenario(scenario, tmp_path, helper=helper, harness="claude", mode="journey", cli=cli)
    assert snapshot(scenario.target) == before
    record = _record(scenario)
    _assert_gate(scenario, record, {"journey"})
    assert cast(dict[str, object], result["gate"])["verdict"] == "promote"
    log = read_events(scenario.out)
    assert {"task", "reflection", "simulator", "judge"} <= {entry["role"] for entry in log}
    simulator_prompts = [cast(str, entry["prompt"]) for entry in log if entry["role"] == "simulator"]
    judge_prompts = [cast(str, entry["prompt"]) for entry in log if entry["role"] == "judge"]
    assert simulator_prompts and all('"reference"' not in prompt and "RUBRIC:" not in prompt
                                     for prompt in simulator_prompts)
    assert judge_prompts and all('"reference"' in prompt and '"transcript"' in prompt
                                 for prompt in judge_prompts)
    held = {cast(str, case["id"]) for case in read_cases(scenario.out) if case["split"] == "holdout"}
    reflection = "\n".join(cast(str, entry["prompt"]) for entry in log if entry["role"] == "reflection")
    assert held and all(f"maple-{case_id[1:]}" not in reflection and f"MAPLE-{case_id[1:]}" not in reflection
                        for case_id in held)
    applied = export_and_apply(scenario, tmp_path, cli=cli)
    source = "unseen\n"
    if helper:
        fixture = applied / "fixture.md"
        _ = fixture.write_text(source)
        done = subprocess.run(["/usr/bin/python3", "-I", str(applied / "scripts/transform.py"), "fixture.md"],
                              cwd=applied, capture_output=True, text=True, check=False)
        assert done.returncode == 0
        assert json.loads(done.stdout) == {"text": source.upper()}
    else:
        assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()
    export_text = "\n".join(path.read_text() for path in scenario.export.iterdir() if path.is_file())
    assert not any(cast(str, item["brief"]) in export_text for item in scenario.draft)
    assert not any(f"maple-{case_id[1:]}" in export_text or f"MAPLE-{case_id[1:]}" in export_text
                   for case_id in held)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
@pytest.mark.parametrize("cli", [CLI, BUNDLE], ids=["source", "bundle"])
def test_mixed_manifest_scores_both_kinds_in_sealed_holdout(tmp_path: Path, cli: Path) -> None:
    scenario = journey_scenario(tmp_path, mixed=True)
    before = snapshot(scenario.target)
    _ = run_scenario(scenario, tmp_path, harness="claude", mode="journey", cli=cli)
    assert snapshot(scenario.target) == before
    record = _record(scenario)
    _assert_gate(scenario, record, {"task", "journey"})
    held_kinds = [case["kind"] for case in read_cases(scenario.out) if case["split"] == "holdout"]
    assert held_kinds.count("task") >= 6 and held_kinds.count("journey") >= 2
    applied = export_and_apply(scenario, tmp_path, cli=cli)
    assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()


@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
def test_failed_journey_command_gate_never_calls_judge(tmp_path: Path) -> None:
    scenario = journey_scenario(tmp_path, failed_gate=True)
    _ = run_scenario(scenario, tmp_path, harness="claude", mode="journey")
    record = _record(scenario)
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    scored = [item for item in outcomes if item.get("case_id") is not None]
    assert scored and all(item.get("status") == "gate-failed" and
                          cast(dict[str, object], item["scores"])["judge"] is None for item in scored)
    log = read_events(scenario.out)
    assert "task" in {entry["role"] for entry in log}
    assert not any(entry["role"] == "judge" for entry in log)


@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
@pytest.mark.parametrize("harness", ["codex", "command"])
def test_unsupported_journey_refuses_before_task_inference(tmp_path: Path, harness: str) -> None:
    scenario = journey_scenario(tmp_path)
    args = ["run", "--target", str(scenario.target), "--out", str(scenario.out),
            "--model", "offline-test", "--harness", harness, "--live", "--edit", "prose"]
    bin_dir = None
    if harness == "command":
        args += command_adapter(scenario)
    else:
        bin_dir = make_vendor_bin(scenario, tmp_path, harness, "normal")
    code, _, error = call_cli(args, tmp_path, bin_dirs=[bin_dir] if bin_dir else ())
    assert code == 1 and json.loads(error)["code"] == "cases-missing"
    _ = (scenario.out / "cases.draft.json").write_text(json.dumps(scenario.draft))
    code, cases, error = call_cli(args, tmp_path, bin_dirs=[bin_dir] if bin_dir else ())
    assert code == 1 and json.loads(error)["code"] == "cases-unapproved"
    approved = ["--approve-cases", cast(str, cases["case_hash"])]
    code, budget, error = call_cli(args + approved, tmp_path, bin_dirs=[bin_dir] if bin_dir else ())
    assert code == 1 and json.loads(error)["code"] == "budget-unapproved"
    approved += ["--approve-budget", str(cast(dict[str, int], budget["estimate"])["calls"])]
    code, result, error = call_cli(args + approved, tmp_path, bin_dirs=[bin_dir] if bin_dir else ())
    assert code == 1 and json.loads(error)["code"] == "journey-unsupported", result
    assert not any("Ask which value to transform" in json.dumps(entry) for entry in read_events(scenario.out)
                   if entry.get("role") == "task")
