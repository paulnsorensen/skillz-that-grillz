from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import mapping
from skillz_experiments._records import read

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "skills/skillz/scripts/skillz-experiment.pyz"


def test_runtime_exclusion_is_exact_and_rejects_symlinks(tmp_path: Path) -> None:
    _ = (tmp_path / "SKILL.md").write_text("seed")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    runtime = scripts / "skillz-experiment.pyz"
    _ = runtime.write_bytes(b"\xff" * 300000)
    assert Candidate.capture(tmp_path, ["SKILL.md"]).files == {"SKILL.md": "seed"}
    other = scripts / "other.pyz"
    _ = other.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="scripts/other.pyz is not UTF-8"):
        _ = Candidate.capture(tmp_path, ["SKILL.md"])
    other.unlink()
    runtime.unlink()
    runtime.symlink_to(tmp_path / "SKILL.md")
    with pytest.raises(ValueError, match="symlinks"):
        _ = Candidate.capture(tmp_path, ["SKILL.md"])


def test_installed_bundle_loads_public_fixtures_outside_checkout(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    _ = shutil.copytree(ROOT / "skills/skillz", installed)
    out = tmp_path / "prepared"
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(installed / "scripts/skillz-experiment.pyz"),
         "self-test", "--model", "offline", "--prepare-only", "--out", str(out)],
        cwd="/", env={"PATH": os.defpath, "SHIV_ROOT": str(tmp_path / "cache")},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (out / "manifest.json").is_file()


def run_installed_self_test(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    installed = tmp_path / "installed"
    _ = shutil.copytree(ROOT / "skills/skillz", installed)
    wrapper = tmp_path / "wrapper.py"
    _ = shutil.copyfile(Path(__file__).parent / "fixtures/command_wrapper.py", wrapper)
    settings = tmp_path / "settings.json"
    log = tmp_path / "requests.jsonl"
    _ = settings.write_text(json.dumps({"log": str(log)}))
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "protocol-fixture",
                                     "command": [sys.executable, str(wrapper), str(settings)]}))
    run = tmp_path / "run"
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(installed / "scripts/skillz-experiment.pyz"), "self-test",
         "--model", "offline", "--harness-config", str(config), "--live", "--out", str(run),
         "--max-invocations", "40"],
        cwd="/", env={"PATH": os.defpath, "SHIV_ROOT": str(tmp_path / "cache")},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return installed, wrapper, settings, run


def test_bundle_runs_actual_gepa_and_evaluator_from_installed_target(tmp_path: Path) -> None:
    installed, wrapper, settings, run = run_installed_self_test(tmp_path)
    record = read(run / "run.json")
    assert record["phase"] == "complete"
    assert record["holdout_consumed"] is True
    arms = mapping(record["arms"])
    assert set(arms) == {"original", "prompt", "prompt-cli"}
    assert "OPTIMIZED" in str(mapping(arms["prompt"])["SKILL.md"])
    assert "scripts/skillz-experiment.pyz" not in mapping(record["seed"])
    outcomes = cast(list[dict[str, object]], record["outcomes"])
    assert any(item["split"] == "reflection" for item in outcomes)
    assert all(mapping(item["usage"])["input_tokens"] is None for item in outcomes)
    holdout = [item for item in outcomes if item["split"] == "holdout"]
    assert len(holdout) == 6
    assert all(item["loaded"] and item["helper_executed"] for item in holdout)
    assert all(item["score"] == 1 for item in holdout if item["arm"] != "original")
    assert record["improvement"] == "inconclusive-bounded-smoke-test"
    assert (installed / "SKILL.md").read_text() == (ROOT / "skills/skillz/SKILL.md").read_text()

    assert record["token_comparison"] == "inconclusive-unknown-usage"
    destination = tmp_path / "export"
    exported = subprocess.run(
        [sys.executable, "-I", "-S", str(installed / "scripts/skillz-experiment.pyz"), "export", str(run),
         "--out", str(destination)], cwd="/",
        env={"PATH": os.defpath, "SHIV_ROOT": str(tmp_path / "cache")},
        capture_output=True, text=True, timeout=60,
    )
    assert exported.returncode == 0, exported.stderr
    report_text = (destination / "report.json").read_text()
    assert str(wrapper) not in report_text and str(settings) not in report_text
    report = read(destination / "report.json")
    assert report["token_comparison"] == "inconclusive-unknown-usage"
    assert mapping(mapping(report["harness"])["judge"])["model"] == "offline"
