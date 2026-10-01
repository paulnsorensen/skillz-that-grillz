"""Exercise the published template as source and through an installed launcher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, cast
from zipfile import ZipFile

import pytest

from wedge._digest import content_sha256


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
        (b"gouda\nbrie\nedam\nbrie\n", ["--minimum", "2"], [{"value": "brie", "count": 2}], 0),
        (b"\xef\xbb\xbfbrie\nbrie\n", [], [{"value": "brie", "count": 2}], 0),
        (b"brie\x0cedam\nbrie\x0cedam\n", [], [{"value": "brie\x0cedam", "count": 2}], 0),
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


def _wedge(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "import wedge; wedge.main()", *args],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def _template_project(repo_root: Path, dest: Path) -> Path:
    """A uv project that holds fromargs source and a skills/records directory.

    The project's own lock supplies the closure. Its fromargs source is
    vendored through ``include``, as ``packaging.md`` documents.
    """
    fromargs = repo_root / "lib/fromargs"
    dest.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        _ = shutil.copy2(fromargs / name, dest / name)
    _ = shutil.copytree(
        fromargs / "src/fromargs", dest / "src/fromargs", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    skill = dest / "skills/records"
    skill.mkdir(parents=True)
    template = repo_root / "skills/wedge/assets"
    _ = shutil.copy2(template / "records.py", skill / "records.py")
    _ = shutil.copy2(template / "wedge.toml", skill / "wedge.toml")
    return skill


def _apply_documented_edits(skill: Path) -> None:
    manifest = skill / "wedge.toml"
    text = manifest.read_text(encoding="utf-8").replace(
        "REPLACE-WITH-OWNER-SLASH-NAME", "example-owner/records-skill",
    )
    _ = manifest.write_text(text.rstrip("\n") + '\ninclude = ["../../src/fromargs"]\n', encoding="utf-8")


def test_template_placeholder_repo_fails_lock(repo_root: Path, tmp_path: Path) -> None:
    skill = _template_project(repo_root, tmp_path / "project")
    result = _wedge(tmp_path / "project", "lock", str(skill.relative_to(tmp_path / "project")))
    assert result.returncode != 0
    assert "'repo' must be 'owner/name'" in result.stderr


def test_template_installed_launcher(repo_root: Path, tmp_path: Path, rewrite_pyz: Callable[..., Path]) -> None:
    project = tmp_path / "project"
    skill = _template_project(repo_root, project)
    _apply_documented_edits(skill)
    relative = str(skill.relative_to(project))

    locked = _wedge(project, "lock", relative)
    assert locked.returncode == 0, locked.stderr
    lock_digest = cast(dict[str, dict[str, str]], json.loads(locked.stdout))["records"]["content_sha256"]
    built_result = _wedge(project, "build", relative, "--out", str(tmp_path / "dist"))
    assert built_result.returncode == 0, built_result.stderr
    built = cast(dict[str, dict[str, str]], json.loads(built_result.stdout))["records"]
    assert built["content_sha256"] == lock_digest
    built_path = Path(built["path"])
    assert content_sha256(built_path) == lock_digest

    installed = tmp_path / "installed"
    _ = shutil.copytree(skill / "scripts", installed / "scripts")
    caller = tmp_path / "caller"
    caller.mkdir()
    _ = (caller / "records.txt").write_text("brie\nbrie\n", encoding="utf-8")
    launcher = installed / "scripts/records"
    result = _run(launcher, caller, "counts", "records.txt", pyz=built_path)
    assert (result.returncode, result.stderr) == (0, "")
    assert json.loads(result.stdout) == [{"value": "brie", "count": 2}]

    with ZipFile(built_path) as archive:
        member = next(name for name in archive.namelist() if name.endswith("records.py"))
        original = archive.read(member)
    tampered = rewrite_pyz(built_path, tmp_path / "tampered.pyz", replace={member: original + b"\n# changed\n"})
    rejected = _run(launcher, caller, "counts", "records.txt", pyz=tampered)
    assert (rejected.returncode, rejected.stdout) == (3, "")
    error = cast(dict[str, object], json.loads(rejected.stderr))
    assert error["exit_code"] == 3
    assert "content sha256 does not match" in str(error["error"])
