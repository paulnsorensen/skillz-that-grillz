"""A malformed ``ac`` marker fails collection with the test and the expected form."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CONFTEST = Path(__file__).with_name("conftest.py")
INI = "[pytest]\nmarkers =\n    ac(id): ties a test to an acceptance-criterion ID\naddopts = --strict-markers -p no:cacheprovider\n"


def _collect(directory: Path, argument: str) -> subprocess.CompletedProcess[str]:
    _ = (directory / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    _ = (directory / "pytest.ini").write_text(INI, encoding="utf-8")
    body = f"import pytest\n\n\n@pytest.mark.ac({argument})\ndef test_marked() -> None:\n    pass\n"
    _ = (directory / "test_marked.py").write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--rootdir", str(directory)],
        cwd=directory, capture_output=True, text=True, check=False,
    )


@pytest.mark.ac("gh-267:AC-1")
@pytest.mark.parametrize("argument", ['"AC-1"', '"Demo:AC-1"', '"demo:ac-1"', '"demo:AC-1", "demo:AC-2"', "1"])
def test_a_malformed_ac_marker_fails_collection_and_names_the_test(tmp_path: Path, argument: str) -> None:
    done = _collect(tmp_path, argument)
    output = done.stdout + done.stderr
    assert done.returncode == 4, output
    assert "test_marked.py::test_marked" in output
    assert "<source>:<id>" in output


@pytest.mark.ac("gh-267:AC-1")
def test_a_qualified_ac_marker_collects(tmp_path: Path) -> None:
    done = _collect(tmp_path, '"demo:AC-1"')
    assert done.returncode == 0, done.stdout + done.stderr
    assert "test_marked.py::test_marked" in done.stdout
