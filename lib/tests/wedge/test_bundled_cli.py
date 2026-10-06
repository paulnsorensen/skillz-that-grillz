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

from wedge._build import build

_WEDGE_SKILL_DIR = Path(__file__).resolve().parents[3] / "skills" / "skillz" / "wedge"


def test_bundled_shiv_pin_matches_the_wedge_library_pin(repo_root: Path) -> None:
    # shiv writes its version into every archive, so both builders must agree.
    bundle = cast(dict[str, dict[str, list[str]]], tomllib.loads((repo_root / "pyproject.toml").read_text()))
    library = cast(dict[str, dict[str, list[str]]], tomllib.loads((repo_root / "lib" / "pyproject.toml").read_text()))
    bundled = [dep for dep in bundle["dependency-groups"]["wedge"] if dep.startswith("shiv")]
    pinned = [dep for dep in library["project"]["dependencies"] if dep.startswith("shiv")]
    assert bundled == pinned


def _base_python() -> str:
    """An interpreter outside this venv, so shiv is importable only from the archive."""
    return getattr(sys, "_base_executable", sys.executable)


@pytest.fixture(scope="module")
def wedge_pyz(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build(_WEDGE_SKILL_DIR, tmp_path_factory.mktemp("wedge-cli")).path


def test_bundled_wedge_builds_a_skill_under_an_interpreter_without_shiv(
    wedge_pyz: Path, fixture_skill_dir: Path, tmp_path: Path
) -> None:
    base = _base_python()
    probe = subprocess.run([base, "-I", "-c", "import shiv"], capture_output=True)
    assert probe.returncode != 0, f"{base} imports shiv; the test cannot prove the archive supplies it"
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
