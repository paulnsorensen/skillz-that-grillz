"""Run a wedge archive under an older real Python and inspect its process result."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast
from zipfile import ZipFile

import pytest

from wedge._main import MAIN_SOURCE
from wedge._zipapp import write_archive


def _python(version: str) -> Path:
    result = subprocess.run(
        ["uv", "python", "find", version],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.skip(f"Python {version} is unavailable: {result.stderr}")
    return Path(result.stdout.strip())


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    site.mkdir()
    _ = (site / "probe.py").write_text(
        """import json
import sys

def main():
    print(json.dumps({'version': list(sys.version_info[:2]), 'args': sys.argv[1:], 'isolated': sys.flags.isolated, 'ignore_environment': sys.flags.ignore_environment, 'no_user_site': sys.flags.no_user_site, 'no_site': sys.flags.no_site}))
"""
    )
    target = tmp_path / "probe.pyz"
    write_archive(site, "probe:main", target, trees={})
    with ZipFile(target) as bundle:
        assert not any(name.startswith("cpython-") for name in bundle.namelist())
    return target


def _run(
    archive: Path, tmp_path: Path, candidates: tuple[str, ...], *, isolated: bool = False,
    flags: tuple[str, ...] = (), environment: dict[str, str] | None = None,
    broken: tuple[str, ...] = (), misnamed: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "bin"
    path.mkdir()
    for version in candidates:
        executable = _python(version)
        (path / f"python{version.rsplit('.', 1)[0]}").symlink_to(executable)
    for version in broken:
        wrapper = path / f"python{version.rsplit('.', 1)[0]}"
        _ = wrapper.write_text(f"#!{tmp_path / 'missing-python'}\n")
        wrapper.chmod(0o755)
    for version in misnamed:
        wrapper = path / f"python{version.rsplit('.', 1)[0]}"
        _ = wrapper.write_text(f'#!/bin/sh\nexec "{_python("3.9.25")}" "$@"\n')
        wrapper.chmod(0o755)
    args = ["one two", "", "--option=value with spaces"]
    return subprocess.run(
        [str(_python("3.9.25")), *(["-I"] if isolated else []), *flags, str(archive), *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PATH": str(path), **(environment or {})},
        timeout=15,
        check=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX PATH re-exec only")
@pytest.mark.ac("gh-581:AC-7")
@pytest.mark.parametrize(
    ("candidates", "expected", "isolated"),
    [
        (("3.11.18", "3.15.2"), [3, 15], True),
        (("3.11.18",), [3, 11], False),
    ],
)
def test_old_python_reexecs_newest_pin_with_arguments(
    archive: Path, tmp_path: Path, candidates: tuple[str, ...], expected: list[int], isolated: bool
) -> None:
    result = _run(archive, tmp_path, candidates, isolated=isolated)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "version": expected,
        "args": ["one two", "", "--option=value with spaces"],
        "isolated": int(isolated),
        "ignore_environment": int(isolated),
        "no_user_site": int(isolated),
        "no_site": 0,
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX PATH re-exec only")
@pytest.mark.ac("gh-581:AC-7")
@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (("-E",), {"ignore_environment": 1, "no_user_site": 0, "no_site": 0}),
        (("-s",), {"ignore_environment": 0, "no_user_site": 1, "no_site": 0}),
        (("-S",), {"ignore_environment": 0, "no_user_site": 0, "no_site": 1}),
        (("-I",), {"ignore_environment": 1, "no_user_site": 1, "no_site": 0}),
        (("-I", "-S"), {"ignore_environment": 1, "no_user_site": 1, "no_site": 1}),
    ],
)
def test_old_python_reexec_preserves_startup_restrictions(
    archive: Path, tmp_path: Path, flags: tuple[str, ...], expected: dict[str, int]
) -> None:
    environment = {"PYTHONHOME": str(tmp_path / "hostile")} if "-E" in flags else {}
    result = _run(archive, tmp_path, ("3.15.2",), flags=flags, environment=environment)
    assert result.returncode == 0, result.stderr
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert payload["version"] == [3, 15]
    assert payload["args"] == ["one two", "", "--option=value with spaces"]
    assert payload["isolated"] == int("-I" in flags)
    assert {key: payload[key] for key in expected} == expected


@pytest.mark.skipif(os.name != "posix", reason="POSIX PATH re-exec only")
@pytest.mark.ac("gh-581:AC-7")
@pytest.mark.parametrize("misnamed", [False, True])
def test_old_python_skips_unusable_newest_pin(
    archive: Path, tmp_path: Path, misnamed: bool
) -> None:
    result = _run(
        archive, tmp_path, ("3.11.18",), isolated=True, flags=("-S",),
        broken=() if misnamed else ("3.15.2",), misnamed=("3.15.2",) if misnamed else (),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "version": [3, 11],
        "args": ["one two", "", "--option=value with spaces"],
        "isolated": 1,
        "ignore_environment": 1,
        "no_user_site": 1,
        "no_site": 1,
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX PATH re-exec only")
@pytest.mark.ac("gh-581:AC-7")
@pytest.mark.parametrize("misnamed", [False, True])
def test_old_python_with_only_unusable_pins_reports_coded_error(
    archive: Path, tmp_path: Path, misnamed: bool
) -> None:
    invalid = ("3.15.2", "3.11.18")
    result = _run(archive, tmp_path, (), broken=() if misnamed else invalid, misnamed=invalid if misnamed else ())
    assert result.returncode == 3
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": "Python 3.9 found; Python 3.11 or newer required",
        "exit_code": 3,
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX PATH re-exec only")
@pytest.mark.ac("gh-581:AC-7")
def test_old_python_without_pin_reports_coded_error(archive: Path, tmp_path: Path) -> None:
    result = _run(archive, tmp_path, ())
    assert result.returncode == 3
    assert result.stdout == ""
    error = cast(dict[str, str | int], json.loads(result.stderr))
    assert error["exit_code"] == 3
    message = error["error"]
    assert isinstance(message, str)
    assert "3.9" in message
    assert "3.11" in message

@pytest.mark.ac("gh-581:AC-7")
def test_old_python_on_non_posix_reports_error_without_path_search(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = io.StringIO()
    prelude = MAIN_SOURCE.split("site_path =", 1)[0]

    def fail_if_searched(_: str) -> str | None:
        pytest.fail("non-POSIX searched PATH")
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt")
        patch.setattr(sys, "version_info", (3, 9))
        patch.setattr(sys, "argv", [str(archive)])
        patch.setattr(sys, "stderr", stderr)
        patch.setattr(shutil, "which", fail_if_searched)
        with pytest.raises(SystemExit) as failure:
            exec(compile(prelude, str(archive), "exec"), {})
    assert failure.value.code == 3
    error = cast(dict[str, str | int], json.loads(stderr.getvalue()))
    assert error["exit_code"] == 3
    message = error["error"]
    assert isinstance(message, str)
    assert "3.9" in message
    assert "3.11" in message
