"""AC-W3: the built .pyz runs standalone under a clean interpreter."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from wedge._build import build


@pytest.fixture(scope="module")
def built_pyz(tmp_path_factory: pytest.TempPathFactory, fixture_skill_dir: Path) -> Path:
    out_dir = tmp_path_factory.mktemp("wedge-pyz-runtime")
    result = build(fixture_skill_dir, out_dir)
    return result.path


def _run_pyz(pyz: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(pyz), *args],
        capture_output=True,
        text=True,
        cwd="/",
        env={"PATH": "/usr/bin:/bin"},
    )


@pytest.mark.ac("AC-W3")
def test_pyz_runs_under_a_clean_interpreter_with_no_project_on_path(built_pyz: Path) -> None:
    result = _run_pyz(built_pyz, "--json", "wheels", "list")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert payload


@pytest.mark.ac("AC-W3")
def test_pyz_self_heals_a_merged_argument(built_pyz: Path) -> None:
    result = _run_pyz(built_pyz, "age", "brie", "--weeks", "2 --dry-run")
    assert result.returncode == 0, result.stderr
    assert "note:" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
