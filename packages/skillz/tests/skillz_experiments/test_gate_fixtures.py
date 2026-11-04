"""`validate` runs each gate file on its pass and fail fixtures and on a null trace (gh-657)."""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from skillz_experiments import _gate_files
from skillz_experiments._seed import seed_cases
from skillz_experiments._validate import validate

ROOT = Path(__file__).resolve().parents[4]
JOURNEY_KIT = Path(__file__).parent / "fixtures/culture-journey"
GATE = "evals/gates/journey.py"
BASE = (ROOT / "packages/skillz/src/skillz_experiments/_journey_gate.py").read_text(encoding="utf-8")
RECEIPTS = 'RECEIPTS: list[Matcher] = [Matcher("checked", tool="Bash", input="validate")]'
ANSWER_GATE = ("import json\nimport pathlib\n\nanswer = pathlib.Path('output/answer.txt')\n"
               + "text = answer.read_text() if answer.is_file() else ''\nprint(json.dumps({'score': 1 if 'ok' in text else 0}))\n")
Finding = dict[str, object]


def _claude(command: str) -> list[str]:
    block = {"type": "tool_use", "name": "Bash", "input": {"command": command}}
    return [json.dumps({"type": "assistant", "message": {"content": [block]}})]


def _codex(command: str) -> list[str]:
    """A Codex agent turn is an `agentMessage`, so the flow opens with one."""
    items = [{"type": "agentMessage", "text": "start"},
             {"type": "commandExecution", "id": "c1", "exitCode": 0, "command": command}]
    return [json.dumps({"method": "item/completed", "params": {"item": item}}) for item in items]


def _journey(tmp_path: Path, receipts: bool = True, gate: str | None = None) -> Path:
    target = tmp_path / "culture"
    _ = shutil.copytree(JOURNEY_KIT, target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    (target / "evals/gates/journey.py.fixture").unlink()
    source = gate or (BASE.replace("RECEIPTS: list[Matcher] = []", RECEIPTS) if receipts else BASE)
    _ = (target / GATE).write_text(source, encoding="utf-8")
    return target


def _task(tmp_path: Path, source: str = ANSWER_GATE) -> Path:
    target = tmp_path / "toy"
    (target / "evals/gates").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("---\nname: toy\ndescription: A toy.\n---\n# Toy\n", encoding="utf-8")
    _ = (target / GATE).write_text(source, encoding="utf-8")
    contract: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "toy", "invocation": "$toy",
                                   "kinds": {"task": {"grader": "command", "gate": GATE}}, "editable": []}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract), encoding="utf-8")
    return target


def _fixture(target: Path, side: str, answer: str | None = None, trace: list[str] | None = None) -> None:
    base = target / "evals/gates/journey.fixtures" / side
    (base / "output").mkdir(parents=True)
    if answer is not None:
        _ = (base / "output/answer.txt").write_text(answer, encoding="utf-8")
    if trace is not None:
        (base / "trace").mkdir()
        _ = (base / "trace/events.jsonl").write_text("".join(line + "\n" for line in trace), encoding="utf-8")


def _gate_findings(target: Path) -> list[Finding]:
    return [item for item in cast(list[Finding], validate(target)["findings"]) if str(item["code"]).startswith("gate-")]


@pytest.mark.ac("gh-657:AC-1")
def test_a_pass_fixture_that_the_gate_does_not_score_one_is_unpassable(tmp_path: Path) -> None:
    target = _task(tmp_path)
    _fixture(target, "pass", answer="nothing")
    found = [item for item in _gate_findings(target) if item["code"] == "gate-unpassable"]
    assert len(found) == 1 and found[0]["severity"] == "error" and found[0]["file"] == GATE
    assert validate(target)["ok"] is False
    _fixture_ok = target / "evals/gates/journey.fixtures/pass/output/answer.txt"
    _ = _fixture_ok.write_text("ok", encoding="utf-8")
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-unpassable"]


@pytest.mark.ac("gh-657:AC-2")
def test_a_fail_fixture_with_a_score_above_zero_is_permissive(tmp_path: Path) -> None:
    target = _task(tmp_path)
    _fixture(target, "fail", answer="ok")
    found = [item for item in _gate_findings(target) if item["code"] == "gate-permissive"]
    assert len(found) == 1 and found[0]["severity"] == "error"
    _ = (target / "evals/gates/journey.fixtures/fail/output/answer.txt").write_text("bad", encoding="utf-8")
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-permissive"]


@pytest.mark.ac("gh-657:AC-1")
@pytest.mark.parametrize("source", ["print('not json')\n", "import sys\nprint('{\"score\": 1}')\nsys.exit(1)\n",
                                    "print('{\"score\": true}')\n"])
def test_a_gate_that_prints_no_valid_score_or_exits_nonzero_is_unpassable(tmp_path: Path, source: str) -> None:
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert [item["code"] for item in _gate_findings(target)] == ["gate-unpassable", "gate-fixture-missing"]


@pytest.mark.ac("gh-657:AC-3")
def test_a_journey_gate_that_scores_a_null_trace_above_zero_warns(tmp_path: Path) -> None:
    target = _journey(tmp_path, receipts=False)
    found = [item for item in _gate_findings(target) if item["code"] == "gate-null-trace"]
    assert len(found) == 1 and found[0]["severity"] == "warning" and found[0]["file"] == GATE


@pytest.mark.ac("gh-657:AC-3")
def test_a_journey_gate_with_receipts_scores_a_null_trace_zero(tmp_path: Path) -> None:
    assert not [item for item in _gate_findings(_journey(tmp_path)) if item["code"] == "gate-null-trace"]


@pytest.mark.ac("gh-657:AC-3")
def test_a_task_kind_gets_no_null_trace_check(tmp_path: Path) -> None:
    target = _task(tmp_path, "import json\nprint(json.dumps({'score': 1}))\n")
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-null-trace"]


@pytest.mark.ac("gh-657:AC-4")
def test_a_gate_with_no_fixtures_warns_and_leaves_the_case_identity_alone(tmp_path: Path) -> None:
    target = _journey(tmp_path)
    bare, seed = validate(target), json.dumps(seed_cases(target), sort_keys=True)
    found = [item for item in cast(list[Finding], bare["findings"]) if item["code"] == "gate-fixture-missing"]
    assert len(found) == 1 and found[0]["severity"] == "warning" and found[0]["file"] == GATE
    _fixture(target, "pass", trace=_claude("just validate"))
    _fixture(target, "fail", trace=_claude("ls"))
    full = validate(target)
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-fixture-missing"]
    assert json.dumps(seed_cases(target), sort_keys=True) == seed
    assert full["cases"] == bare["cases"] and full["split"] == bare["split"] and full["contract"] == bare["contract"]


@pytest.mark.ac("gh-657:AC-5")
@pytest.mark.parametrize("encode", [_claude, _codex])
def test_a_fixture_trace_in_either_harness_encoding_runs_the_gate(tmp_path: Path, encode: Callable[[str], list[str]]) -> None:
    target = _journey(tmp_path)
    _fixture(target, "pass", trace=encode("just validate"))
    _fixture(target, "fail", trace=encode("ls"))
    assert [item for item in _gate_findings(target) if item["code"] != "gate-null-trace"] == []


@pytest.mark.ac("gh-657:AC-5")
def test_a_gate_runs_isolated_in_a_temporary_directory(tmp_path: Path) -> None:
    source = ("import json\nimport pathlib\nimport sys\npathlib.Path('marker').write_text('x')\n"
              + "print(json.dumps({'score': 1 if sys.flags.isolated else 0}))\n")
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert [item for item in _gate_findings(target) if item["severity"] == "error"] == []
    assert not list(tmp_path.rglob("marker")) and not list(Path.cwd().glob("marker"))


@pytest.mark.ac("gh-657:AC-5")
def test_a_gate_that_outlives_the_timeout_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gate_files, "FIXTURE_SECONDS", 0.5)
    target = _task(tmp_path, "import time\ntime.sleep(30)\n")
    _fixture(target, "pass", answer="ok")
    found = [item for item in _gate_findings(target) if item["code"] == "gate-fixture-timeout"]
    assert len(found) == 1 and found[0]["severity"] == "error"
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-unpassable"]


@pytest.mark.ac("gh-657:AC-1")
@pytest.mark.ac("gh-657:AC-2")
def test_the_shipped_journey_gate_passes_and_fails_its_own_fixtures() -> None:
    assert _gate_findings(ROOT / "skills/skillz") == []
