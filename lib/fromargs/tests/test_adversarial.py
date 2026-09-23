"""Adversarial attacks on the fromargs contract (AC-1 to AC-10)."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable

import pytest
from cyclopts import App

import fromargs

JsonLine = Callable[[str], dict[str, object]]


def _app(calls: list[dict[str, object]]) -> App:
    app = App()

    @app.command
    def show(name: str, *, json: bool = False, count: int = 1) -> None:
        calls.append({"name": name, "json": json, "count": count})
        if name == "fail":
            raise fromargs.CliError("line one\nline two", exit_code=4)

    return app


def test_json_envelope_carries_did_you_mean(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=["show", "x", "--json", "--cont", "2"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    envelope = single_json_line(captured.err)
    assert envelope["exit_code"] == 2
    assert "Did you mean --count?" in str(envelope["error"])
    assert calls == []


def test_json_envelope_stays_one_line_for_multiline_message(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=["show", "fail", "--json"]) == 4

    envelope = single_json_line(capsys.readouterr().err)
    assert envelope == {"error": "line one\nline two", "exit_code": 4}


def test_json_inside_split_token_selects_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=["show", "fail", "--count", "2 --json"]) == 4

    err_lines = capsys.readouterr().err.splitlines()
    assert err_lines[0].startswith("note: split quoted argument")
    assert json.loads(err_lines[1]) == {"error": "line one\nline two", "exit_code": 4}
    assert calls == [{"name": "fail", "json": True, "count": 2}]


def test_lone_leading_json_is_usage_error_without_note(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=["--json"]) == 2

    captured = capsys.readouterr()
    assert "note:" not in captured.err
    assert single_json_line(captured.err)["exit_code"] == 2
    assert calls == []


def test_default_command_runs_on_empty_argv(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    app = App()

    @app.default
    def main() -> None:
        calls.append("main")

    assert fromargs.run(app, argv=[]) == 0
    assert calls == ["main"]
    assert capsys.readouterr().err == ""


def test_argv_defaults_to_sys_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(sys, "argv", ["prog", "show", "x", "--count", "5"])

    assert fromargs.run(_app(calls)) == 0
    assert calls == [{"name": "x", "json": False, "count": 5}]


def test_run_never_reads_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingStdin(io.StringIO):
        def read(self, size: int | None = -1) -> str:
            raise AssertionError("fromargs must not read a stdin payload")

        def readline(self, size: int | None = -1) -> str:
            raise AssertionError("fromargs must not read a stdin payload")

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(sys, "stdin", ExplodingStdin())

    assert fromargs.run(_app(calls), argv=["show", "x", "--json"]) == 0
    assert calls == [{"name": "x", "json": True, "count": 1}]
