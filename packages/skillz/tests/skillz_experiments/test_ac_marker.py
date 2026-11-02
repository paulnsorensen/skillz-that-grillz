"""A malformed ``ac`` marker fails collection with the test and the expected form."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CONFTEST = Path(__file__).parents[1] / "conftest.py"
INI = "[pytest]\nmarkers =\n    ac(id): ties a test to an acceptance-criterion ID\naddopts = --strict-markers -p no:cacheprovider\n"
MALFORMED = ('"AC-1"', '"Demo:AC-1"', '"demo:ac-1"', '"demo:AC-1", "demo:AC-2"', "1")
# The conftest stops collection at the first bad marker, so each case needs its own collection.
# One interpreter runs every collection, with a separator line before each result. Each case uses the same module
# names, so the driver drops the cached case modules before each collection.
DRIVER = """
import contextlib, io, sys
import pytest
for directory in sys.argv[1:]:
    for name in [name for name in sys.modules if name.split('.')[0] in ('conftest', 'test_marked')]:
        del sys.modules[name]
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = int(pytest.main(['--collect-only', '-q', '--import-mode=importlib', '--rootdir', directory, directory]))
    print(f'=== {directory} {code}')
    print(output.getvalue())
"""


def _case(directory: Path, argument: str) -> Path:
    directory.mkdir()
    _ = (directory / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    _ = (directory / "pytest.ini").write_text(INI, encoding="utf-8")
    body = f"import pytest\n\n\n@pytest.mark.ac({argument})\ndef test_marked() -> None:\n    pass\n"
    _ = (directory / "test_marked.py").write_text(body, encoding="utf-8")
    return directory


def _collect(directories: list[Path]) -> dict[str, tuple[int, str]]:
    done = subprocess.run([sys.executable, "-c", DRIVER, *map(str, directories)],
                          cwd=directories[0].parent, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    results: dict[str, tuple[int, str]] = {}
    for block in done.stdout.split("=== ")[1:]:
        head, _, output = block.partition("\n")
        directory, code = head.rsplit(" ", 1)
        results[directory] = (int(code), output)
    return results


@pytest.mark.ac("gh-267:AC-1")
def test_a_malformed_ac_marker_fails_collection_and_names_the_test(tmp_path: Path) -> None:
    directories = [_case(tmp_path / f"case{index}", argument) for index, argument in enumerate(MALFORMED)]
    results = _collect(directories)
    assert sorted(results) == sorted(map(str, directories))
    for argument, directory in zip(MALFORMED, directories):
        code, output = results[str(directory)]
        assert code == 4, (argument, output)
        assert "test_marked.py::test_marked" in output, (argument, output)
        assert "<source>:<id>" in output, (argument, output)


@pytest.mark.ac("gh-267:AC-1")
def test_a_qualified_ac_marker_collects(tmp_path: Path) -> None:
    directory = _case(tmp_path / "good", '"demo:AC-1"')
    code, output = _collect([directory])[str(directory)]
    assert code == 0, output
    assert "test_marked.py::test_marked" in output