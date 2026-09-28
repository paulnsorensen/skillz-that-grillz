"""Exercise the published template as source and through an installed launcher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from wedge._build import build
from wedge._lock import lock


def _run(script: Path, cwd: Path, *args: str, pyz: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SHIV_ROOT": str(cwd / "shiv")}
    if pyz is not None:
        env["WEDGE_PYZ"] = str(pyz)
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=cwd, env=env,
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize(
    ("content", "options", "expected", "status"),
    [
        (b"gouda\nbrie\nedam\nbrie\n", [], [{"value": "brie", "count": 2}, {"value": "edam", "count": 1}], 0),
        (b"gouda\nbrie\nedam\nbrie\n", ["--full"], [
            {"value": "brie", "count": 2}, {"value": "edam", "count": 1}, {"value": "gouda", "count": 1},
        ], 0),
        (b" \n\n", [], [], 0),
        (b"brie\n", ["--minimum", "0"], "--minimum must be at least 1", 2),
        (b"\xff", [], "cannot read", 3),
    ],
)
def test_template_source_contract(
    repo_root: Path, tmp_path: Path, content: bytes, options: list[str], expected: object, status: int,
) -> None:
    source = repo_root / "skills/wedge/assets/records.py"
    data = tmp_path / "records.txt"
    _ = data.write_bytes(content)
    result = _run(source, tmp_path, "counts", str(data), *options)
    assert result.returncode == status, result.stderr
    if status:
        assert result.stdout == ""
        error = cast(dict[str, object], json.loads(result.stderr))
        assert error["exit_code"] == status
        assert str(expected) in str(error["error"])
    else:
        assert json.loads(result.stdout) == expected
        assert result.stderr.startswith("note:") if content.count(b"\n") > 2 and not options else result.stderr == ""
        repeated = _run(source, tmp_path, "counts", str(data), *options)
        assert (repeated.returncode, repeated.stdout, repeated.stderr) == (status, result.stdout, result.stderr)


def test_template_installed_launcher(
    repo_root: Path, tmp_path: Path, copy_repo_subset: Callable[[Path], Path],
) -> None:
    skill_dir = copy_repo_subset(tmp_path / "checkout")
    template = repo_root / "skills/wedge/assets"
    _ = shutil.copy2(template / "records.py", skill_dir / "records.py")
    _ = shutil.copy2(template / "wedge.toml", skill_dir / "wedge.toml")
    locked = lock(skill_dir)
    built = build(skill_dir, tmp_path / "dist")
    assert built.sha256 == locked.sha256
    installed = tmp_path / "installed"
    _ = shutil.copytree(skill_dir / "scripts", installed / "scripts")
    caller = tmp_path / "caller"
    caller.mkdir()
    _ = (caller / "records.txt").write_text("brie\nbrie\n", encoding="utf-8")
    launcher = installed / "scripts/records"
    result = _run(launcher, caller, "counts", "records.txt", pyz=built.path)
    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == [{"value": "brie", "count": 2}]
    _ = built.path.write_bytes(built.path.read_bytes() + b"tampered")
    rejected = _run(launcher, caller, "counts", "records.txt", pyz=built.path)
    assert (rejected.returncode, rejected.stdout) == (3, "")
    error = cast(dict[str, object], json.loads(rejected.stderr))
    assert error["exit_code"] == 3
    assert "sha256 does not match" in str(error["error"])
