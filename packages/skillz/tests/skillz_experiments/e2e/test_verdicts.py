"""Joined command-adapter search, gate, export, and replay tests."""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path
from typing import cast

import pytest

from support import BUNDLE, CLI, FIXTURES, export_and_apply, make_scenario, run_scenario, snapshot


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-3")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-5")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-10")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-2")
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
@pytest.mark.parametrize("cli", [CLI, BUNDLE], ids=["source", "bundle"])
def test_command_prose_promotes_after_actual_file_transformation(tmp_path: Path, harness: str, cli: Path) -> None:
    scenario = make_scenario(tmp_path)
    original_tree = snapshot(scenario.target)
    result = run_scenario(scenario, tmp_path, harness=harness, cli=cli)
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"], gate["cases"], gate["repeats"]) == (
        "promote", 1.0, 0.0, 6, 3)
    record = cast(dict[str, object], json.loads((scenario.out / "run.json").read_text()))
    held = [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])
            if cast(dict[str, object], item).get("split") == "holdout"]
    assert len(held) == 36
    cases = cast(list[dict[str, object]], cast(dict[str, object], json.loads(
        (scenario.out / "cases.json").read_text()))["cases"])
    identifiers = {case["id"] for case in cases if case["split"] == "holdout"}
    assert len(identifiers) == 6
    for identifier in identifiers:
        for arm, score in (("baseline", 0.0), ("winner", 1.0)):
            observed = [item for item in held if item["case_id"] == identifier and item["arm"] == arm]
            assert sorted(cast(int, item["repeat"]) for item in observed) == [0, 1, 2]
            assert {item["score"] for item in observed} == {score}
            assert all(item["loaded"] and item["helper_executed"] for item in observed)
    assert snapshot(scenario.target) == original_tree
    applied = export_and_apply(scenario, tmp_path, cli=cli)
    assert "Uppercase fixture.md." in (applied / "SKILL.md").read_text()
    for path in scenario.export.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        text = path.read_text()
        assert all(cast(str, cast(dict[str, object], case["expected"])["text"]) not in text
                   for case in scenario.draft)
    workspace = tmp_path / "replay"
    staged = workspace / ".agents/skills/transform-skill"
    staged.parent.mkdir(parents=True)
    _ = shutil.copytree(applied, staged)
    _ = (staged / "EXPERIMENT_MARKER").write_text("replay")
    _ = (workspace / "fixture.md").write_text("unseen-case\n")
    replay = subprocess.run(["/usr/bin/python3", "-I", str(FIXTURES / "replay.py"), str(workspace)],
                            cwd=workspace, capture_output=True, text=True, check=False)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(cast(dict[str, str], json.loads(replay.stdout))["result_json"]) == {
        "text": "UNSEEN-CASE\n"}


@pytest.mark.ac("skillz-experiments-end-to-end:AC-1")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-3")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-5")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-10")
@pytest.mark.ac("skillz-experiments-end-to-end:AC-2")
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
@pytest.mark.parametrize("cli", [CLI, BUNDLE], ids=["source", "bundle"])
def test_command_helper_executes_winning_export(tmp_path: Path, harness: str, cli: Path) -> None:
    scenario = make_scenario(tmp_path, helper=True)
    original_tree = snapshot(scenario.target)
    result = run_scenario(scenario, tmp_path, helper=True, harness=harness, cli=cli)
    assert cast(dict[str, object], result["gate"])["verdict"] == "promote"
    assert snapshot(scenario.target) == original_tree
    applied = export_and_apply(scenario, tmp_path, cli=cli)
    fixture = applied / "fixture.md"
    _ = fixture.write_text("unseen-case\n")
    run = subprocess.run(["/usr/bin/python3", "-I", str(applied / "scripts/transform.py"), "fixture.md"],
                         cwd=applied, capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert json.loads(run.stdout) == {"text": "UNSEEN-CASE\n"}


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
def test_changed_winner_ties_on_holdout(tmp_path: Path, harness: str) -> None:
    scenario = make_scenario(tmp_path)
    held_families = {"f9", "f7", "f5", "f3", "f0", "f4"}
    for case in scenario.draft:
        if case["family"] in held_families:
            source = cast(dict[str, str], case["files"])["fixture.md"].upper()
            cast(dict[str, str], case["files"])["fixture.md"] = source
    result = run_scenario(scenario, tmp_path, harness=harness, extra_flags=("--seed", "1"))
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["delta"], gate["se"], gate["repeats"]) == (
        "inconclusive", 0.0, 0.0, 3)
    record = cast(dict[str, object], json.loads((scenario.out / "run.json").read_text()))
    assert cast(dict[str, object], record["search"])["retained_seed"] is False
    assert cast(dict[str, str], record["winner"])["SKILL.md"] != scenario.original
    held = [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])
            if cast(dict[str, object], item).get("split") == "holdout"]
    assert len(held) == 36
    assert {item["case_id"] for item in held} == {f"t{int(family[1:])}" for family in held_families}
    assert {item["score"] for item in held} == {1.0}
    assert (scenario.target / "SKILL.md").read_text() == scenario.original


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
def test_protected_family_regression_rejects(tmp_path: Path, harness: str) -> None:
    scenario = make_scenario(tmp_path)
    for case in scenario.draft:
        if case["family"] == "f9":
            cast(dict[str, str], case["files"])["fixture.md"] = "PROTECTED-9\n"
            cast(dict[str, str], case["expected"])["text"] = "PROTECTED-9\n"
    extra = dict(scenario.draft[9])
    extra["id"] = "t10"
    extra["files"] = {"fixture.md": "PROTECTED-10\n"}
    extra["expected"] = {"text": "PROTECTED-10\n"}
    scenario.draft.append(extra)
    result = run_scenario(scenario, tmp_path, harness=harness, mode="regress", extra_flags=("--seed", "1", "--family-budget", "0"))
    gate = cast(dict[str, object], result["gate"])
    assert gate["verdict"] == "reject"
    assert gate["reasons"] == ["family-regression:f9"]
    assert gate["cases"] == 6 and gate["repeats"] == 3
    record = cast(dict[str, object], json.loads((scenario.out / "run.json").read_text()))
    assert cast(dict[str, object], record["search"])["retained_seed"] is False
    held = [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])
            if cast(dict[str, object], item).get("split") == "holdout"]
    assert len(held) == 36
    protected = [item for item in held if item["case_id"] in {"t9", "t10"}]
    assert len(protected) == 12
    assert {item["score"] for item in protected if item["arm"] == "baseline"} == {1.0}
    assert {item["score"] for item in protected if item["arm"] == "winner"} == {0.0}


@pytest.mark.ac("skillz-experiments-end-to-end:AC-4")
@pytest.mark.parametrize("harness", ["command", "claude", "codex"])
def test_changed_winner_is_globally_worse_on_holdout(tmp_path: Path, harness: str) -> None:
    scenario = make_scenario(tmp_path)
    held_families = {"f9", "f7", "f5", "f3", "f0", "f4"}
    for case in scenario.draft:
        if case["family"] in held_families:
            source = f"PROTECTED-{str(case['id']).upper()}\n"
            cast(dict[str, str], case["files"])["fixture.md"] = source
            cast(dict[str, str], case["expected"])["text"] = source
    result = run_scenario(scenario, tmp_path, harness=harness, mode="regress", extra_flags=("--seed", "1"))
    gate = cast(dict[str, object], result["gate"])
    assert (gate["verdict"], gate["reasons"], gate["delta"], gate["se"], gate["cases"],
            gate["n_shared"], gate["repeats"]) == ("reject", [], -1.0, 0.0, 6, 6, 3)
    record = cast(dict[str, object], json.loads((scenario.out / "run.json").read_text()))
    assert cast(dict[str, object], record["search"])["retained_seed"] is False
    assert cast(dict[str, str], record["winner"])["SKILL.md"] != scenario.original
    held = [cast(dict[str, object], item) for item in cast(list[object], record["outcomes"])
            if cast(dict[str, object], item).get("split") == "holdout"]
    assert len(held) == 36
    assert {item["case_id"] for item in held} == {f"t{int(family[1:])}" for family in held_families}
    for identifier in {item["case_id"] for item in held}:
        for arm, score in (("baseline", 1.0), ("winner", 0.0)):
            observed = [item for item in held if item["case_id"] == identifier and item["arm"] == arm]
            assert sorted(cast(int, item["repeat"]) for item in observed) == [0, 1, 2]
            assert {item["score"] for item in observed} == {score}
    assert (scenario.target / "SKILL.md").read_text() == scenario.original
