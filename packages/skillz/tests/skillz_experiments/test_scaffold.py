"""The `scaffold` command, the generic journey gate, and the Draft journeys documentation."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from skillz_experiments._cases import CodedError
from skillz_experiments._contract import LOCATION, parse
from skillz_experiments._gate_files import expand_gates
from skillz_experiments._scaffold import scaffold

ROOT = Path(__file__).resolve().parents[4]
BUNDLE = ROOT / "skills/skillz/scripts/skillz-experiment.pyz"
EXPERIMENTS = ROOT / "skills/skillz/references/experiments.md"
GATE_SOURCE = (ROOT / "packages/skillz/src/skillz_experiments/_journey_gate.py").read_text(encoding="utf-8")


def _skill(root: Path) -> Path:
    skill = root / "toy"
    skill.mkdir()
    _ = (skill / "SKILL.md").write_text("---\nname: toy-skill\ndescription: A toy.\n---\n# Toy\n", encoding="utf-8")
    return skill


def _gate(directory: Path) -> dict[str, object]:
    done = subprocess.run([sys.executable, "-I", "-c", GATE_SOURCE], cwd=directory, capture_output=True, text=True, check=True)
    return cast(dict[str, object], json.loads(done.stdout))


def _trace(directory: Path, lines: list[str]) -> None:
    (directory / "trace").mkdir()
    _ = (directory / "trace/events.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _draft_journeys() -> str:
    match = re.search(r"^## Draft journeys.*?(?=^## |\Z)", EXPERIMENTS.read_text(encoding="utf-8"), re.M | re.S)
    assert match is not None
    return match[0]


@pytest.mark.ac("autoimprove-authoring-headroom:AC-6")
def test_the_built_bundle_scaffolds_a_draft_contract_that_loads_once_approved(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    done = subprocess.run([sys.executable, str(BUNDLE), "scaffold", "--target", str(skill)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    written = skill / LOCATION
    data = cast(dict[str, object], json.loads(written.read_text(encoding="utf-8")))
    assert data["schema_version"] == 1 and data["status"] == "draft" and data["skill"] == "toy-skill"
    journey = cast(dict[str, object], cast(dict[str, object], data["kinds"])["journey"])
    assert journey["grader"] == "journey" and journey["gate"] == "evals/gates/journey.py" and "argv" not in journey
    assert (skill / "evals/gates/journey.py").read_text(encoding="utf-8") == GATE_SOURCE
    assert parse(expand_gates(data | {"status": "approved"}, skill), "skill").grader("journey").argv == (
        "python3", "-I", "-c", GATE_SOURCE)
    assert "reference.must" in cast(str, journey["rubric"]) and "reference.must_not" in cast(str, journey["rubric"])
    with pytest.raises(CodedError) as caught:
        _ = parse(data, "skill")
    assert caught.value.code == "contract-unapproved"
    contract = parse(expand_gates(data | {"status": "approved"}, skill), "skill")
    assert contract.skill == "toy-skill" and list(contract.kinds) == ["journey"]
    assert json.loads(done.stdout)["status"] == "draft"


@pytest.mark.ac("autoimprove-authoring-headroom:AC-7")
@pytest.mark.parametrize("link", [False, True], ids=["file", "symlink"])
def test_scaffold_refuses_an_existing_contract_and_leaves_it_unchanged(tmp_path: Path, link: bool) -> None:
    skill = _skill(tmp_path)
    (skill / "evals").mkdir()
    existing = skill / LOCATION
    if link:
        existing.symlink_to(tmp_path / "missing.json")
    else:
        _ = existing.write_text('{"mine": true}\n', encoding="utf-8")
    with pytest.raises(CodedError) as caught:
        _ = scaffold(skill)
    assert caught.value.code == "contract-exists"
    assert existing.is_symlink() if link else existing.read_text(encoding="utf-8") == '{"mine": true}\n'


@pytest.mark.ac("autoimprove-authoring-headroom:AC-7")
def test_scaffold_refuses_a_target_that_is_not_a_skill_and_writes_nothing(tmp_path: Path) -> None:
    (tmp_path / "deep").mkdir()
    with pytest.raises(CodedError) as caught:
        _ = scaffold(tmp_path)
    assert caught.value.code == "usage" and "SKILL.md" in str(caught.value)
    assert not (tmp_path / "evals").exists()


@pytest.mark.ac("autoimprove-authoring-headroom:AC-7")
def test_scaffold_refuses_a_symlinked_evals_directory_and_writes_nothing_through_it(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (skill / "evals").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(CodedError) as caught:
        _ = scaffold(skill)
    assert caught.value.code == "usage" and "symlink" in str(caught.value)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.ac("autoimprove-authoring-headroom:AC-6")
def test_scaffold_reads_only_the_skill_file_and_not_the_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill = _skill(tmp_path)

    def walk(_self: Path, _pattern: str) -> object:
        raise AssertionError("scaffold walked the target tree")

    monkeypatch.setattr(Path, "rglob", walk)
    assert scaffold(skill)["status"] == "draft"

@pytest.mark.ac("autoimprove-authoring-headroom:AC-8")
def test_the_gate_scores_0_with_no_trace(tmp_path: Path) -> None:
    assert _gate(tmp_path) == {"score": 0, "failed": ["no-trace"]}


@pytest.mark.ac("autoimprove-authoring-headroom:AC-8")
def test_the_gate_scores_0_when_no_event_is_an_agent_turn(tmp_path: Path) -> None:
    _trace(tmp_path, ['{"type": "user"}', "not json", '{"type": "result"}', "[1]"])
    assert _gate(tmp_path) == {"score": 0, "failed": ["no-agent-turn"]}


@pytest.mark.ac("autoimprove-authoring-headroom:AC-8")
def test_the_gate_scores_1_with_an_agent_turn(tmp_path: Path) -> None:
    _trace(tmp_path, ['{"type": "user"}', '{"type": "assistant"}'])
    assert _gate(tmp_path) == {"score": 1, "failed": []}


@pytest.mark.ac("autoimprove-authoring-headroom:AC-9")
def test_draft_journeys_explains_each_field_scaffold_and_the_too_easy_stop() -> None:
    section = _draft_journeys()
    for name in ("brief", "max_turns", "requirements", "expected.must", "expected.must_not", "expected.questions",
                 "scaffold", "contract-exists", "cases-too-easy", "new run directory", "trap"):
        assert f"`{name}`" in section or name in section, name
    assert "Journeys stay hand-authored" not in EXPERIMENTS.read_text(encoding="utf-8")


@pytest.mark.ac("autoimprove-authoring-headroom:AC-10")
def test_draft_journeys_mines_sessions_with_redaction_and_never_copies_the_solution() -> None:
    section = _draft_journeys()
    for phrase in ("user correction", "a retry", "a failed tool call", "long multi-turn exchange", "source: session",
                   "before the solution", "home paths, emails, hostnames, and tokens",
                   "Never copy the solution of the session into `expected`", "#132"):
        assert phrase in section, phrase


@pytest.mark.ac("autoimprove-authoring-headroom:AC-8")
def test_importing_the_gate_prints_nothing_and_runs_no_check(tmp_path: Path) -> None:
    gate = ROOT / "packages/skillz/src/skillz_experiments/_journey_gate.py"
    code = f"import runpy; runpy.run_path({str(gate)!r}, run_name='gate_lib')"
    done = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert done.stdout == ""
