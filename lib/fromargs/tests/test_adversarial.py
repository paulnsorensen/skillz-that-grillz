"""Adversarial attacks on the fromargs.App contract."""

from __future__ import annotations

import io
import json
import sys
from typing import TYPE_CHECKING

import pytest

import fromargs

if TYPE_CHECKING:
    from conftest import JsonLine


def _app(calls: list[tuple[str, dict[str, object]]]) -> fromargs.App:
    app = fromargs.App("t")

    @app.command
    def show(name: str, *, count: int = 1) -> None:
        calls.append(("show", {"name": name, "count": count}))

    @app.command
    def fail(kind: str) -> None:
        calls.append(("fail", {"kind": kind}))
        if kind == "multiline":
            raise fromargs.CliError("line one\nline two", exit_code=4)
        raise fromargs.CliError("boom")

    return app


def test_split_that_reveals_json_is_healed(capsys: pytest.CaptureFixture[str]) -> None:
    """A split candidate is probed with --json already stripped, so it heals."""
    calls: list[tuple[str, dict[str, object]]] = []

    assert _app(calls).run(["show", "x", "--count", "2 --json"]) == 0

    captured = capsys.readouterr()
    assert captured.err.strip() == "note: split quoted argument '2 --json' into ['2', '--json']"
    assert captured.out == ""
    assert calls == [("show", {"name": "x", "count": 2})]


def test_split_that_reveals_full_is_honored(capsys: pytest.CaptureFixture[str]) -> None:
    """A split candidate is probed with --full already stripped, and --full still turns off truncation."""
    calls: list[tuple[str, dict[str, object]]] = []
    app = fromargs.App("t")

    @app.command(limit=2)
    def rank(name: str, *, top: int) -> list[int]:
        calls.append(("rank", {"name": name, "top": top}))
        return list(range(top))

    assert app.run(["rank", "x", "--top", "5 --full"]) == 0

    captured = capsys.readouterr()
    assert captured.err.strip() == "note: split quoted argument '5 --full' into ['5', '--full']"
    assert json.loads(captured.out) == [0, 1, 2, 3, 4]
    assert calls == [("rank", {"name": "x", "top": 5})]


def test_lone_leading_json_before_empty_argv_is_command_required(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert _app(calls).run(["--json"]) == 2

    captured = capsys.readouterr()
    assert "note:" not in captured.err
    assert single_json_line(captured.err) == {"error": "command required", "exit_code": 2}
    assert calls == []


def test_default_command_runs_on_empty_argv(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    app = fromargs.App("t")

    @app._cyclopts.default
    def main() -> None:
        calls.append("main")

    assert app.run([]) == 0
    assert calls == ["main"]
    assert capsys.readouterr().err == ""


def test_argv_defaults_to_sys_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sys, "argv", ["prog", "show", "x", "--count", "5"])

    assert _app(calls).run() == 0
    assert calls == [("show", {"name": "x", "count": 5})]


def test_run_never_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingStdin(io.StringIO):
        def read(self, size: int | None = -1) -> str:
            raise AssertionError("fromargs must not read a stdin payload")

        def readline(self, size: int | None = -1) -> str:
            raise AssertionError("fromargs must not read a stdin payload")

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sys, "stdin", ExplodingStdin())

    assert _app(calls).run(["show", "x", "--json"]) == 0
    assert calls == [("show", {"name": "x", "count": 1})]