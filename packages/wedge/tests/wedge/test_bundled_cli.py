"""The /skillz skill's bundled wedge.pyz builds a skill without a wedge project."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import pytest

from wedge._build import build, build_many

_SKILLZ_DIR = Path(__file__).resolve().parents[4] / "skills" / "skillz"


def test_wedge_package_has_no_shiv_dependency(repo_root: Path) -> None:
    bundle = cast(dict[str, dict[str, list[str]]], tomllib.loads((repo_root / "pyproject.toml").read_text()))
    library = cast(dict[str, dict[str, list[str]]], tomllib.loads((repo_root / "packages" / "wedge" / "pyproject.toml").read_text()))
    assert "wedge" not in bundle["dependency-groups"]
    assert not any(dep.startswith("shiv") for dep in library["project"]["dependencies"])


def _base_python() -> str:
    """Use an interpreter outside this venv to prove the archive is self-contained."""
    return getattr(sys, "_base_executable", sys.executable)


@pytest.fixture(scope="module")
def wedge_pyz(tmp_path_factory: pytest.TempPathFactory) -> Path:
    outcomes = build_many([_SKILLZ_DIR], tmp_path_factory.mktemp("wedge-cli"))
    results = [o.value for o in outcomes if o.value is not None and o.value.name == "wedge"]
    assert len(results) == 1, [o.error for o in outcomes]
    return results[0].path


@pytest.mark.ac("gh-581:AC-11")
def test_bundled_wedge_builds_a_skill_under_an_interpreter_without_shiv(
    wedge_pyz: Path, fixture_skill_dir: Path, tmp_path: Path
) -> None:
    base = _base_python()
    uv = shutil.which("uv")
    assert uv is not None
    out_dir = tmp_path / "out"

    result = subprocess.run(
        [base, "-I", str(wedge_pyz), "build", str(fixture_skill_dir), "--out", str(out_dir)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "PATH": os.pathsep.join([str(Path(uv).parent), "/usr/bin", "/bin"]),
            "HOME": str(tmp_path / "home"),
        },
    )

    assert result.returncode == 0, result.stderr
    manifest = cast(dict[str, dict[str, object]], json.loads(result.stdout))
    built = Path(cast(str, manifest["cheese-cave"]["path"]))
    assert built.is_file()
    expected = build(fixture_skill_dir, tmp_path / "reference")
    assert manifest["cheese-cave"]["content_sha256"] == expected.content_sha256
