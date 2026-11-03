"""In-process tests of the `inspect-skill` CLI entrypoint: exit codes and the JSON on stdout or stderr."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skillz_inspect._cli import main


def test_a_readable_skill_exits_zero_and_prints_its_report_as_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "SKILL.md"
    _ = path.write_text("---\nname: x\ndescription: y\n---\nShort body.\n")
    assert main([str(path)]) == 0
    captured = capsys.readouterr()
    report = cast(dict[str, object], json.loads(captured.out))
    assert report["schema_version"] == 4 and report["long_sentences"] == []
    assert captured.err == ""


@pytest.mark.parametrize("raw", [b"---\nname: x\n", b"---\n\xff\xfe\n---\nbody\n"])
def test_a_malformed_skill_exits_three_with_an_inspect_error_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: bytes
) -> None:
    path = tmp_path / "SKILL.md"
    _ = path.write_bytes(raw)
    assert main([str(path)]) == 3
    captured = capsys.readouterr()
    error = cast(dict[str, object], json.loads(captured.err))
    assert captured.out == "" and str(error["error"]).startswith("inspect: ")


def test_a_missing_file_exits_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "absent.md")]) == 3
    captured = capsys.readouterr()
    assert captured.out == "" and "error" in json.loads(captured.err)


def test_a_missing_path_argument_is_an_argument_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) != 0
    assert capsys.readouterr().out == ""
