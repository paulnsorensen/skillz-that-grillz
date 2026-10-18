"""Joined Claude journey search, holdout, and export tests."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from support import (BUNDLE, CLI, FIXTURES, Scenario, approve_and_run, assert_promoted, call_cli, cli_error,
                     command_adapter, export_and_apply, make_scenario, make_vendor_bin, read_cases, read_events,
                     read_record, run_scenario, snapshot)


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
            "expected": {"must": [label.upper() + "\n"]},
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


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
@pytest.mark.parametrize("helper, cli", [
    pytest.param(False, CLI, id="J0-source"), pytest.param(True, CLI, id="J1-source"),
    pytest.param(False, BUNDLE, id="J0-bundle")])
def test_claude_journey_promotes_from_actual_conversation_and_export(
        tmp_path: Path, helper: bool, cli: Path) -> None:
    scenario = journey_scenario(tmp_path, helper=helper)
    before = snapshot(scenario.target)
    result = run_scenario(scenario, tmp_path, helper=helper, harness="claude", mode="journey", cli=cli)
    assert snapshot(scenario.target) == before
    assert_promoted(scenario, result, kinds=frozenset({"journey"}))
    log = read_events(scenario.out)
    assert {"task", "reflection", "simulator", "judge"} <= {entry["role"] for entry in log}
    simulator_prompts = [cast(str, entry["prompt"]) for entry in log if entry["role"] == "simulator"]
    judge_prompts = [cast(str, entry["prompt"]) for entry in log if entry["role"] == "judge"]
    assert simulator_prompts and all('"reference"' not in prompt and "RUBRIC:" not in prompt
                                     for prompt in simulator_prompts)
    assert judge_prompts and all('"reference"' in prompt and '"transcript"' in prompt
                                 for prompt in judge_prompts)
    task_messages = [cast(str, entry["message"]) for entry in log if entry["role"] == "task"]
    briefs = [cast(str, item["brief"]) for item in scenario.draft]
    assert task_messages and not any(
        brief in message or "Answer only the agent's question" in message or "reference" in message
        or "MAPLE-" in message for message in task_messages for brief in briefs)
    # The simulator reply may carry the label; the opening request must not.
    assert not any("maple-" in message for message in task_messages if not message.startswith("Value: "))
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


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
def test_mixed_manifest_scores_both_kinds_in_sealed_holdout(tmp_path: Path) -> None:
    scenario = journey_scenario(tmp_path, mixed=True)
    before = snapshot(scenario.target)
    result = run_scenario(scenario, tmp_path, harness="claude", mode="journey")
    assert snapshot(scenario.target) == before
    assert_promoted(scenario, result, kinds=frozenset({"task", "journey"}), cases=None)
    held_kinds = [case["kind"] for case in read_cases(scenario.out) if case["split"] == "holdout"]
    assert held_kinds.count("task") >= 6 and held_kinds.count("journey") >= 2
    applied = export_and_apply(scenario, tmp_path)
    assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()


@pytest.mark.ac("skillz-experiments-end-to-end:AC-7")
def test_failed_journey_command_gate_never_calls_judge(tmp_path: Path) -> None:
    scenario = journey_scenario(tmp_path, failed_gate=True)
    _ = run_scenario(scenario, tmp_path, harness="claude", mode="journey")
    record = read_record(scenario.out)
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
    scenario.out.mkdir(mode=0o700, exist_ok=True)
    _ = (scenario.out / "cases.draft.json").write_text(json.dumps(scenario.draft))
    code, result, error = call_cli(args, tmp_path, bin_dirs=[bin_dir] if bin_dir else ())
    # The stop comes before the case and budget approvals, so no approval data and no task call exist.
    assert code == 1 and cli_error(error)["code"] == "journey-unsupported", (result, error)
    assert "case_hash" not in result and not (scenario.out / "run.json").exists()
    assert not any(entry.get("role") == "task" for entry in read_events(scenario.out))
