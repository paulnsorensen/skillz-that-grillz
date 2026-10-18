"""Grader gates as files under evals/gates (gh-367)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import CodedError
from skillz_experiments._contract import load_contract, parse
from skillz_experiments._records import read
from skillz_experiments._scaffold import scaffold
from skillz_experiments.run import Stop, run

pytestmark = pytest.mark.usefixtures("host_login")

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).parent / "fixtures"
MODEL = "local-test"
GATE = "evals/gates/check.py"
SCRIPT = "import json\nprint(json.dumps({'score': 1}))\n"
Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]


def _target(root: Path, files: dict[str, str] | None = None, kind: dict[str, object] | None = None,
            editable: list[str] | None = None) -> Path:
    target = root / "toy"
    (target / "evals").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("---\nname: toy\ndescription: A toy.\n---\n# Toy\n", encoding="utf-8")
    for name, text in {GATE: SCRIPT, **(files or {})}.items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        _ = (target / name).write_text(text, encoding="utf-8")
    contract = {"schema_version": 1, "status": "approved", "skill": "toy", "invocation": "$toy",
                "kinds": {"task": kind or {"grader": "command", "gate": GATE}}, "editable": editable or []}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract), encoding="utf-8")
    return target


@pytest.mark.ac("gh-367:AC-1")
def test_a_gate_path_loads_as_the_inline_argv_of_the_file_text(tmp_path: Path) -> None:
    contract = load_contract(_target(tmp_path))
    assert contract.grader("task").argv == ("python3", "-I", "-c", SCRIPT)
    done = subprocess.run([sys.executable, "-I", "-c", contract.grader("task").argv[3]], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == {"score": 1}


@pytest.mark.ac("gh-367:AC-2")
def test_the_identity_follows_the_gate_and_its_sibling_modules(tmp_path: Path) -> None:
    target = _target(tmp_path, {"evals/gates/_lib.py": "VALUE = 1\n",
                                GATE: "import json\nimport _lib\nprint(json.dumps({'score': _lib.VALUE}))\n"})
    first = load_contract(target)
    assert first.grader("task").argv[3] != SCRIPT
    done = subprocess.run([sys.executable, "-I", "-c", first.grader("task").argv[3]], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == {"score": 1}
    sibling, gate = target / "evals/gates/_lib.py", target / GATE
    sibling_bytes, gate_bytes = sibling.read_bytes(), gate.read_bytes()
    _ = sibling.write_bytes(sibling_bytes + b"# x\n")
    assert load_contract(target).identity != first.identity
    _ = sibling.write_bytes(sibling_bytes)
    assert load_contract(target).identity == first.identity
    _ = gate.write_bytes(gate_bytes + b"# x\n")
    assert load_contract(target).identity != first.identity
    _ = gate.write_bytes(gate_bytes)
    assert load_contract(target).identity == first.identity


@pytest.mark.ac("gh-367:AC-3")
def test_a_gate_file_keeps_the_identity_of_the_inline_form(tmp_path: Path) -> None:
    source = FIXTURES / "culture-journey"
    target = tmp_path / "culture"
    (target / "evals/gates").mkdir(parents=True)
    text = (source / "evals/gates/journey.py.fixture").read_text(encoding="utf-8")
    _ = (target / "evals/gates/journey.py").write_text(text, encoding="utf-8")
    document = cast(dict[str, dict[str, dict[str, object]]], json.loads((source / "evals/autoimprove.json").read_text(encoding="utf-8")))
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(document), encoding="utf-8")
    inline = document["kinds"]["journey"] | {"argv": ["python3", "-I", "-c", text]}
    del inline["gate"]
    expected = parse(document | {"kinds": {"journey": inline}}, "skill")
    assert load_contract(target).identity == expected.identity


@pytest.mark.ac("gh-367:AC-4")
def test_a_candidate_cannot_edit_or_capture_a_gate(tmp_path: Path) -> None:
    for name in (GATE, "evals/gates/_lib.py"):
        files = {"evals/gates/_lib.py": "VALUE = 1\n", GATE: "import _lib\n" + SCRIPT}
        with pytest.raises(CodedError) as caught:
            _ = load_contract(_target(tmp_path / name.replace("/", "-"), files, editable=[name]))
        assert caught.value.code == "contract-gate-editable"
    target = _target(tmp_path / "ok")
    captured = Candidate.capture(target, [], load_contract(target))
    assert "SKILL.md" in captured.files and not any(name.startswith("evals/") for name in captured.files)


def _prepared(tmp_path: Path, make_target: Maker, write_draft: Drafter) -> tuple[Path, Path, str]:
    target, out = make_target(tmp_path), tmp_path / "run"
    (target / "evals/gates").mkdir()
    _ = (target / GATE).write_text(SCRIPT, encoding="utf-8")
    contract: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
                "kinds": {"task": {"grader": "command", "gate": GATE}}, "editable": []}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract), encoding="utf-8")
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, MODEL, live=True)
    case_hash = cast(str, cases.value.data["case_hash"])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, MODEL, live=True, approve_cases=case_hash)
    calls = cast(dict[str, int], budget.value.data["estimate"])["calls"]
    with pytest.raises(Stop) as ready:
        _ = run(target, out, MODEL, approve_cases=case_hash, approve_budget=calls)
    assert ready.value.code == "live-required"
    return target, out, case_hash


@pytest.mark.ac("gh-367:AC-6")
def test_the_run_record_freezes_argv_and_no_gate(tmp_path: Path, make_target: Maker, write_draft: Drafter) -> None:
    _, out, _ = _prepared(tmp_path, make_target, write_draft)
    kind = cast(dict[str, dict[str, dict[str, object]]], read(out / "run.json")["contract"])["kinds"]["task"]
    assert kind["argv"] == ["python3", "-I", "-c", SCRIPT] and "gate" not in kind


@pytest.mark.ac("gh-367:AC-4")
@pytest.mark.parametrize("change", ["overwrite", "delete"])
def test_a_gate_changed_after_prepare_does_not_change_the_resumed_contract(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, change: str) -> None:
    target, out, case_hash = _prepared(tmp_path, make_target, write_draft)
    before = read(out / "run.json")["contract"]
    if change == "delete":
        (target / GATE).unlink()
    else:
        _ = (target / GATE).write_text("print('{\"score\": 0}')\n", encoding="utf-8")
    with pytest.raises(Stop) as resumed:
        _ = run(target, out, MODEL, approve_cases=case_hash)
    assert resumed.value.code == "live-required"
    assert read(out / "run.json")["contract"] == before


def _symlinked(root: Path) -> Path:
    target = _target(root)
    (target / GATE).unlink()
    _ = (root / "elsewhere.py").write_text(SCRIPT, encoding="utf-8")
    (target / GATE).symlink_to(root / "elsewhere.py")
    return target


@pytest.mark.ac("gh-367:AC-5")
@pytest.mark.parametrize(("label", "gate", "files", "code"), [
    ("outside-evals", "scripts/check.py", {"scripts/check.py": SCRIPT}, "contract-gate-invalid"),
    ("not-python", "evals/gates/check.txt", {"evals/gates/check.txt": SCRIPT}, "contract-gate-invalid"),
    ("dotdot", "evals/gates/../check.py", {"evals/check.py": SCRIPT}, "contract-gate-invalid"),
    ("missing", "evals/gates/none.py", {}, "contract-gate-invalid"),
    ("third-party", GATE, {GATE: "import requests\n"}, "contract-gate-invalid"),
    ("shadow", GATE, {GATE: "import json\n", "evals/gates/json.py": "x = 1\n"}, "contract-gate-invalid"),
    ("syntax", GATE, {GATE: "def (\n"}, "contract-gate-invalid"),
    ("cycle", GATE, {GATE: "import _a\n", "evals/gates/_a.py": "import _b\n", "evals/gates/_b.py": "import _a\n"},
     "contract-gate-invalid"),
    ("too-large", GATE, {GATE: "# " + "x" * 70_000 + "\n"}, "contract-gate-invalid"),
], ids=["outside-evals", "not-python", "dotdot", "missing", "third-party", "shadow", "syntax", "cycle", "too-large"])
def test_an_unusable_gate_stops_with_a_coded_error(
        tmp_path: Path, label: str, gate: str, files: dict[str, str], code: str) -> None:
    del label
    target = _target(tmp_path, files, {"grader": "command", "gate": gate})
    with pytest.raises(CodedError) as caught:
        _ = load_contract(target)
    assert caught.value.code == code


@pytest.mark.ac("gh-367:AC-5")
def test_a_symlinked_gate_stops_with_a_coded_error(tmp_path: Path) -> None:
    with pytest.raises(CodedError) as caught:
        _ = load_contract(_symlinked(tmp_path))
    assert caught.value.code == "contract-gate-invalid"


@pytest.mark.ac("gh-367:AC-5")
@pytest.mark.parametrize("kind", [
    {"grader": "command", "gate": GATE, "argv": ["python3", "-c", "pass"]},
    {"grader": "exact-json", "gate": GATE},
])
def test_a_gate_next_to_argv_or_on_a_grader_without_argv_stops(tmp_path: Path, kind: dict[str, object]) -> None:
    with pytest.raises(CodedError) as caught:
        _ = load_contract(_target(tmp_path, kind=kind))
    assert caught.value.code == "contract-gate-invalid"


@pytest.mark.ac("gh-367:AC-7")
def test_the_mode_fixtures_hold_no_argv_and_one_longest() -> None:
    document = cast(dict[str, dict[str, dict[str, dict[str, object]]]],
                    json.loads((FIXTURES / "mode/mode-fixtures.json").read_text(encoding="utf-8")))
    assert all("argv" not in kind and "gate" in kind for kind in document["target"]["kinds"].values())
    found = [path for path in (FIXTURES / "mode").rglob("*.py")
             if path.is_file() and re.search(r"^def longest\b", path.read_text(encoding="utf-8"), re.M)]
    assert found == [FIXTURES / "mode/evals/gates/mode/_markdown.py"]


def _skill(root: Path) -> Path:
    skill = root / "toy"
    skill.mkdir()
    _ = (skill / "SKILL.md").write_text("---\nname: toy-skill\ndescription: A toy.\n---\n# Toy\n", encoding="utf-8")
    return skill


@pytest.mark.ac("gh-367:AC-8")
def test_scaffold_writes_the_gate_file_and_a_contract_that_names_it(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    _ = scaffold(skill)
    source = (ROOT / "packages/skillz/src/skillz_experiments/_journey_gate.py").read_bytes()
    assert (skill / "evals/gates/journey.py").read_bytes() == source
    kind = cast(dict[str, dict[str, dict[str, object]]], json.loads((skill / "evals/autoimprove.json").read_text(encoding="utf-8")))["kinds"]["journey"]
    assert kind["gate"] == "evals/gates/journey.py" and "argv" not in kind


@pytest.mark.ac("gh-367:AC-8")
@pytest.mark.parametrize("existing", ["evals/autoimprove.json", "evals/gates/journey.py"])
def test_scaffold_stops_and_writes_nothing_when_the_contract_or_the_gate_exists(tmp_path: Path, existing: str) -> None:
    skill = _skill(tmp_path)
    (skill / existing).parent.mkdir(parents=True)
    _ = (skill / existing).write_text("mine\n", encoding="utf-8")
    before = sorted(path.relative_to(skill).as_posix() for path in skill.rglob("*"))
    with pytest.raises(CodedError) as caught:
        _ = scaffold(skill)
    assert caught.value.code == "contract-exists"
    assert sorted(path.relative_to(skill).as_posix() for path in skill.rglob("*")) == before
    assert (skill / existing).read_text(encoding="utf-8") == "mine\n"


def test_the_kit_stage_moves_the_gate_into_the_target(tmp_path: Path) -> None:
    kit = ROOT / "packages/skillz/journey-kits/skillz-autoimprove/journeys.py"
    done = subprocess.run([sys.executable, str(kit), "stage", "--approve-contract"], env={**os.environ, "TMPDIR": str(tmp_path)},
                          capture_output=True, text=True, timeout=30, check=True)
    target = Path(next(line for line in done.stdout.splitlines() if line.startswith("target:")).split(maxsplit=1)[1])
    assert load_contract(target).grader("journey").argv[:3] == ("python3", "-I", "-c")


FORWARD_REF = ("from __future__ import annotations\n\nimport json\nimport typing\nfrom dataclasses import dataclass\n\n"
               "@dataclass\nclass Row:\n    value: Later\n\n\nclass Later:\n    pass\n\n\n"
               "print(json.dumps({'score': 1, 'hints': sorted(typing.get_type_hints(Row)), 'name': __name__}))\n")


def _argv_source(root: Path, files: dict[str, str]) -> str:
    return load_contract(_target(root, files)).grader("task").argv[3]


@pytest.mark.ac("gh-367:AC-10")
def test_a_bundled_gate_runs_as_the_real_main_module(tmp_path: Path) -> None:
    single = _argv_source(tmp_path / "single", {GATE: FORWARD_REF})
    bundled = _argv_source(tmp_path / "bundled", {GATE: FORWARD_REF + "import _lib\n", "evals/gates/_lib.py": "VALUE = 1\n"})
    assert bundled != single
    outputs = [subprocess.run([sys.executable, "-I", "-c", source], capture_output=True, text=True, check=True).stdout
               for source in (single, bundled)]
    assert json.loads(outputs[0]) == {"score": 1, "hints": ["value"], "name": "__main__"}
    assert outputs[1] == outputs[0]


@pytest.mark.ac("gh-367:AC-11")
def test_a_gate_named_like_a_stdlib_module_is_not_shadowed_by_itself(tmp_path: Path) -> None:
    gate = "evals/gates/json.py"
    target = _target(tmp_path, {gate: "import json\n" + SCRIPT}, {"grader": "command", "gate": gate})
    assert load_contract(target).grader("task").argv[3] == "import json\n" + SCRIPT
