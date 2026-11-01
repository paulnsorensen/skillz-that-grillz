"""skills/skillz ships its journeys: the contract kind, the one gate copy, the generated file, and a journey-only run."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from skillz_experiments._cli import main
from skillz_experiments._contract import load_contract
from skillz_experiments._seed import seed_cases

ROOT = Path(__file__).resolve().parents[4]
SKILL = ROOT / "skills" / "skillz"
KIT = ROOT / "packages/skillz/journey-kits/skillz-autoimprove/journeys.py"
# The drafting agent adds criteria to the task seeds at intake; this stands in for that step.
DRAFTED = [{"id": "answer", "text": "THE answer SHALL satisfy the request."}]


def _kit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("skillz_autoimprove_journey_kit", KIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.ac("gh-369:AC-6")
def test_the_shipped_contract_has_a_journey_kind_that_runs_the_one_gate_copy() -> None:
    rules = load_contract(SKILL)
    assert sorted(rules.kinds) == ["inspection", "journey", "task"]
    journey = rules.grader("journey")
    assert journey.argv == ("python3", "-I", "-c", (SKILL / "evals/gates/journey.py").read_text(encoding="utf-8"))
    assert journey.rubric == getattr(_kit(), "RUBRIC")
    assert not (KIT.parent / "gate.py").exists()


@pytest.mark.ac("gh-369:AC-6")
def test_the_checked_in_journeys_equal_the_generator_output() -> None:
    generated = cast(list[dict[str, object]], getattr(_kit(), "cases")())
    expected = json.dumps(generated, indent=2) + "\n"
    assert (SKILL / "evals/journeys.json").read_text(encoding="utf-8") == expected
    assert len(generated) == 18 and len({case["family"] for case in generated}) == 6


def _staged(tmp_path: Path) -> Path:
    """Return a private copy of the shipped skill, as a user would run it."""
    target = tmp_path / "staged" / "skillz"
    _ = shutil.copytree(SKILL, target, ignore=shutil.ignore_patterns("wedge.toml", "__pycache__"))
    return target


def _first_stop(target: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], *flags: str) -> dict[str, object]:
    seeded = seed_cases(target)
    assert seeded is not None
    for case in seeded:
        _ = cast(dict[str, object], case["expected"]).setdefault("criteria", DRAFTED)
    out = tmp_path / "run"
    out.mkdir(mode=0o700)
    _ = (out / "cases.draft.json").write_text(json.dumps(seeded), encoding="utf-8")
    _ = main(["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", "prose", "--repeats", "1", *flags])
    stopped = capsys.readouterr()
    assert cast(dict[str, object], json.loads(stopped.err))["code"] == "cases-unapproved", stopped.err
    return cast(dict[str, object], json.loads(stopped.out))


def _marks(data: dict[str, object], kind: str) -> list[str]:
    """Return the scoring mark of each listed case of `kind`."""
    lines = [line for line in str(data["question"]).splitlines() if line.startswith("- ") and f"| kind {kind} |" in line]
    return [line.split(" | ")[5] for line in lines]


@pytest.mark.ac("gh-369:AC-7")
def test_a_journey_only_run_on_the_staged_skill_scores_the_eighteen_journeys_and_stops_for_approval(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _staged(tmp_path)
    data = _first_stop(target, tmp_path, capsys, "--kind", "journey")
    seeded = cast(list[dict[str, object]], seed_cases(target))
    assert _marks(data, "journey") == ["scored"] * 18
    others = [case for case in seeded if case["kind"] != "journey"]
    assert others and _marks(data, "task") == ["not scored"] * len(others)


def test_a_default_run_on_the_staged_skill_scores_the_journeys_with_the_tasks(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = _first_stop(_staged(tmp_path), tmp_path, capsys)
    assert _marks(data, "journey") == ["scored"] * 18
    assert "scored" in _marks(data, "task") and "not scored" not in _marks(data, "task")


def test_a_kit_run_scores_the_eighteen_journeys_under_the_kit_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    done = subprocess.run([sys.executable, str(KIT), "stage", "--approve-contract"], env={**os.environ, "TMPDIR": str(tmp_path)},
                          capture_output=True, text=True, timeout=60, check=True)
    target = Path(next(line for line in done.stdout.splitlines() if line.startswith("target:")).split(maxsplit=1)[1])
    data = _first_stop(target, tmp_path, capsys)
    assert _marks(data, "journey") == ["scored"] * 18 and "kinds journey" in str(data["question"])


def _stop_errors(target: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], edit: str) -> tuple[dict[str, object], dict[str, object]]:
    """Run a journey-only call on the copy, approve its cases with the shown hash, and return both stop errors."""
    seeded = seed_cases(target)
    assert seeded is not None
    out = tmp_path / "run"
    out.mkdir(mode=0o700)
    _ = (out / "cases.draft.json").write_text(json.dumps(seeded), encoding="utf-8")
    command = ["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", edit, "--repeats", "1",
               "--kind", "journey"]
    _ = main(command)
    first = capsys.readouterr()
    error = _coded(first.err)
    if error["code"] != "cases-unapproved":
        return error, error
    shown = cast(dict[str, object], json.loads(first.out))["case_hash"]
    _ = main([*command, "--approve-cases", str(shown)])
    return error, _coded(capsys.readouterr().err)


def _coded(err: str) -> dict[str, object]:
    """Return the coded error: the JSON on the last non-empty stderr line, after any warning lines."""
    return cast(dict[str, object], json.loads(err.strip().splitlines()[-1]))


def _stops(target: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], edit: str) -> tuple[str, str]:
    first, second = _stop_errors(target, tmp_path, capsys, edit)
    return str(first["code"]), str(second["code"])


@pytest.mark.ac("gh-384:AC-1")
def test_a_prose_journey_run_on_a_copy_without_wedge_toml_reaches_the_budget_stop(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = _staged(tmp_path)
    assert not (target / "wedge.toml").exists()
    assert _stops(target, tmp_path, capsys, "prose") == ("cases-unapproved", "budget-unapproved")


@pytest.mark.ac("gh-384:AC-2")
def test_a_prose_cli_run_on_the_same_copy_stops_with_a_clear_code(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, stopped = _stop_errors(_staged(tmp_path), tmp_path, capsys, "prose+cli")
    assert stopped["code"] == "helper-frozen"
    assert "--edit prose" in str(stopped["error"])
