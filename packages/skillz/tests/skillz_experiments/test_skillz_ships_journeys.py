"""skills/skillz ships its journeys: the contract kind, the one gate copy, the generated file, and a journey-only run."""
from __future__ import annotations

import importlib.util
import json
import os
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
    done = subprocess.run([sys.executable, str(KIT), "stage"], env={**os.environ, "TMPDIR": str(tmp_path)},
                          capture_output=True, text=True, timeout=60, check=True)
    return Path(next(line for line in done.stdout.splitlines() if line.startswith("target:")).split(maxsplit=1)[1])


def _first_stop(target: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], *flags: str) -> dict[str, object]:
    seeded = seed_cases(target)
    assert seeded is not None
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
