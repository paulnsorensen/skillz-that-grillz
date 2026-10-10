from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import mapping

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "skills/skillz/scripts/skillz-experiment.pyz"


def test_runtime_pyz_files_are_frozen_and_never_editable_and_a_symlink_is_rejected(tmp_path: Path) -> None:
    _ = (tmp_path / "SKILL.md").write_text("seed")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    runtime = scripts / "skillz-experiment.pyz"
    _ = runtime.write_bytes(b"\xff" * 300000)
    builder = tmp_path / "wedge" / "scripts" / "wedge.pyz"
    builder.parent.mkdir(parents=True)
    _ = builder.write_bytes(b"\xff" * 300000)
    runtime_files = {"scripts/skillz-experiment.pyz", "wedge/scripts/wedge.pyz"}
    first = Candidate.capture(tmp_path, ["SKILL.md"])
    assert first.files == {"SKILL.md": "seed"} and runtime_files <= first.frozen.keys()
    other = scripts / "other.pyz"
    _ = other.write_bytes(b"\xff")
    captured = Candidate.capture(tmp_path, ["SKILL.md"])
    assert captured.files == {"SKILL.md": "seed"}
    assert captured.frozen["scripts/other.pyz"] == b"\xff"
    other.unlink()
    runtime.unlink()
    runtime.symlink_to(tmp_path / "SKILL.md")
    with pytest.raises(ValueError, match="symlinks"):
        _ = Candidate.capture(tmp_path, ["SKILL.md"])


def run_installed(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    installed = tmp_path / "installed"
    if not installed.exists():
        _ = shutil.copytree(ROOT / "skills/skillz", installed)
    return subprocess.run(
        [sys.executable, "-I", "-S", str(installed / "scripts/skillz-experiment.pyz"), *arguments],
        cwd="/", env={"PATH": os.defpath, "SHIV_ROOT": str(tmp_path / "cache")},
        capture_output=True, text=True, timeout=60,
    )


def test_installed_bundle_runs_the_host_checks_outside_checkout(tmp_path: Path) -> None:
    """Without `claude` on PATH, a fresh run stops at the free host checks, before the case draft and any model call."""
    out = tmp_path / "run"
    result = run_installed(tmp_path, "run", "--target", str(tmp_path / "installed"), "--out", str(out),
                           "--model", "offline")
    assert result.returncode == 1, result.stderr
    stop = mapping(cast(object, json.loads(result.stdout)))
    assert stop["stop"] == "host-not-ready" and stop["ok"] is False and stop["live_calls"] == 0
    claude = next(mapping(row) for row in cast(list[object], stop["checks"]) if mapping(row)["check"] == "claude")
    assert claude["status"] == "fail" and claude["fix"]
    assert json.loads(result.stderr)["code"] == "host-not-ready"
    assert not (out / "run.json").exists()


def test_installed_bundle_simulates_the_gate_and_rejects_removed_commands(tmp_path: Path) -> None:
    simulated = run_installed(tmp_path, "self-test", "--simulate")
    assert simulated.returncode == 0, simulated.stderr
    rates = mapping(cast(object, json.loads(simulated.stdout)))
    assert rates["live_calls"] == 0 and cast(float, rates["rate_2se"]) <= 0.06
    removed = run_installed(tmp_path, "baseline", "anything")
    assert removed.returncode == 1 and json.loads(removed.stderr)["code"] == "command-removed"


def test_installed_bundle_carries_the_pinned_search_engine() -> None:
    with zipfile.ZipFile(BUNDLE) as archive:
        assert any(name.startswith("site-packages/gepa/") for name in archive.namelist())
