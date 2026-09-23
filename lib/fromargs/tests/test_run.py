"""Behavior of fromargs.run: dispatch, status mapping, and error rendering."""

# No `from __future__ import annotations`: Cyclopts resolves the Annotated
# hints of commands defined inside tests, which reference local converters.

import asyncio
import io
from collections.abc import Callable, Sequence
from typing import Annotated

import pytest
from cyclopts import App, CycloptsError, Parameter, Token

import fromargs

JsonLine = Callable[[str], dict[str, object]]


def _app(calls: list[tuple[str, dict[str, object]]]) -> App:
    app = App()

    @app.command
    def show(name: str, *, max_count: int = 1, json: bool = False) -> int | None:
        calls.append(("show", {"name": name, "max_count": max_count, "json": json}))
        return None if name == "none" else 7

    @app.command
    def fail(kind: str, *, json: bool = False) -> None:
        calls.append(("fail", {"kind": kind}))
        if kind == "contract":
            raise fromargs.contract_error(ValueError("bad shape"), context="load")
        raise fromargs.CliError("boom", exit_code=3)

    return app


def test_returns_handler_status() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    app = _app(calls)

    assert fromargs.run(app, argv=["show", "x"]) == 7
    assert calls == [("show", {"name": "x", "max_count": 1, "json": False})]

    assert fromargs.run(app, argv=["show", "none"]) == 0
    assert len(calls) == 2


def test_stdout_parameter_captures_handler_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def hello() -> None:
        print("hi")

    buffer = io.StringIO()
    assert fromargs.run(app, argv=["hello"], stdout=buffer) == 0
    assert buffer.getvalue() == "hi\n"
    assert capsys.readouterr().out == ""


def test_non_integer_status_is_a_type_error() -> None:
    app = App()

    @app.command
    def odd() -> str:
        return "yes"

    with pytest.raises(TypeError, match="non-integer status"):
        fromargs.run(app, argv=["odd"])


def test_cli_error_text(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert fromargs.run(_app(calls), argv=["fail", "plain"]) == 3

    captured = capsys.readouterr()
    assert captured.err == "ERROR: boom\n"
    assert captured.out == ""


def test_contract_error_text(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert fromargs.run(_app(calls), argv=["fail", "contract"]) == 3

    captured = capsys.readouterr()
    assert captured.err == "ERROR: load: bad shape\n"
    assert captured.out == ""


def test_cli_error_default_exit_code_is_usage() -> None:
    assert fromargs.CliError("usage").exit_code == 2


def test_json_error_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert fromargs.run(_app(calls), argv=["show", "--json"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    envelope = single_json_line(captured.err)
    assert envelope["exit_code"] == 2
    assert "name" in str(envelope["error"])
    assert calls == []


def test_json_error_envelope_for_handler_error(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert fromargs.run(_app(calls), argv=["fail", "contract", "--json"]) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert single_json_line(captured.err) == {
        "error": "load: bad shape",
        "exit_code": 3,
    }


def test_json_after_double_dash_is_a_literal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def echo(*words: str) -> None:
        raise fromargs.CliError(" ".join(words))

    assert fromargs.run(app, argv=["echo", "--", "--json"]) == 2
    assert capsys.readouterr().err == "ERROR: --json\n"


def test_missing_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert fromargs.run(_app(calls), argv=[]) == 2
    assert capsys.readouterr().err == "ERROR: command required\n"


def test_did_you_mean_surfaces(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    app = _app(calls)

    assert fromargs.run(app, argv=["shw", "x"]) == 2
    assert "Did you mean" in capsys.readouterr().err

    assert fromargs.run(app, argv=["show", "x", "--max_cnt", "2"]) == 2
    assert "Did you mean --max-count?" in capsys.readouterr().err

    assert calls == []


def test_underscore_flag_binds() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert fromargs.run(_app(calls), argv=["show", "x", "--max_count", "3"]) == 7
    assert calls == [("show", {"name": "x", "max_count": 3, "json": False})]


def _converter_app(converted: list[str]) -> App:
    def checked(type_: object, tokens: Sequence[Token]) -> str:
        converted.append(tokens[0].value)
        if tokens[0].value == "bad":
            raise fromargs.CliError("custom bad value", exit_code=4)
        return tokens[0].value

    app = App()

    @app.command
    def fetch(
        item: Annotated[str, Parameter(converter=checked)] = "",
        *,
        count: int = 0,
        json: bool = False,
    ) -> None:
        pass

    return app


def test_converter_cli_error_is_reported(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    converted: list[str] = []

    assert fromargs.run(_converter_app(converted), argv=["fetch", "bad"]) == 4
    assert capsys.readouterr().err == "ERROR: custom bad value\n"

    assert fromargs.run(_converter_app(converted), argv=["fetch", "bad", "--json"]) == 4
    assert single_json_line(capsys.readouterr().err) == {
        "error": "custom bad value",
        "exit_code": 4,
    }


def test_valid_argv_is_parsed_once() -> None:
    converted: list[str] = []

    assert fromargs.run(_converter_app(converted), argv=["fetch", "ok"]) == 0
    assert converted == ["ok"]


def test_probe_hides_converter_error_of_rejected_candidate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    converted: list[str] = []
    app = _converter_app(converted)
    argv = ["fetch", "--count", "2 --item bad"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""

    assert fromargs.run(app, argv=argv) == 2
    assert "Invalid value for --count" in capsys.readouterr().err


def test_converter_rejection_still_tries_repair(
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: list[tuple[list[str], int]] = []

    def no_spaces(type_: object, tokens: Sequence[Token]) -> list[str]:
        values = [token.value for token in tokens]
        if any(" " in value for value in values):
            raise fromargs.CliError(f"bad {values}", exit_code=4)
        return values

    app = App()

    @app.command
    def go(
        *, tags: Annotated[list[str], Parameter(converter=no_spaces)], limit: int = 0
    ) -> None:
        received.append((tags, limit))

    assert fromargs.run(app, argv=["go", "--tags", "a --limit 3"]) == 0
    assert received == [(["a"], 3)]
    assert "note: split quoted argument" in capsys.readouterr().err


def test_handler_cyclopts_error_propagates() -> None:
    app = App()

    @app.command
    def inner() -> None:
        raise CycloptsError(msg="inner")

    with pytest.raises(CycloptsError):
        fromargs.run(app, argv=["inner"])


def test_json_equals_true_selects_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    app = _app(calls)

    assert fromargs.run(app, argv=["show", "--json=true"]) == 2
    assert single_json_line(capsys.readouterr().err)["exit_code"] == 2

    assert fromargs.run(app, argv=["show", "--json=false"]) == 2
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_json_alias_selects_envelope_for_handler_error(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    app = App()

    @app.command
    def boom(
        *, json: Annotated[bool, Parameter(name=["--json", "-j"])] = False
    ) -> None:
        raise fromargs.CliError("boom")

    assert fromargs.run(app, argv=["boom", "-j"]) == 2
    assert single_json_line(capsys.readouterr().err) == {
        "error": "boom",
        "exit_code": 2,
    }


def test_non_bool_json_parameter_keeps_text_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def dump(*, json: str = "") -> None:
        raise fromargs.CliError("disk full")

    assert fromargs.run(app, argv=["dump", "--json", "out.json"]) == 2
    assert capsys.readouterr().err == "ERROR: disk full\n"


def test_json_detection_honors_disabled_delimiter(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    app = App(end_of_options_delimiter="")

    @app.command
    def echo(*words: str, json: bool = False) -> None:
        pass

    assert fromargs.run(app, argv=["echo", "--", "--json", "--bogus"]) == 2
    assert single_json_line(capsys.readouterr().err)["exit_code"] == 2


def test_json_detection_honors_subcommand_delimiter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()
    sub = App(name="sub", end_of_options_delimiter="++")
    app.command(sub)

    @sub.default
    def main(*words: str, json: bool = False) -> None:
        raise fromargs.CliError(" ".join(words))

    assert fromargs.run(app, argv=["sub", "x", "++", "--json"]) == 2
    assert capsys.readouterr().err == "ERROR: x --json\n"


def test_bool_status_is_a_type_error() -> None:
    app = App()

    @app.command
    def yes() -> bool:
        return True

    with pytest.raises(TypeError, match="non-integer status: True"):
        fromargs.run(app, argv=["yes"])


def test_async_handler_is_awaited() -> None:
    app = App()

    @app.command
    async def later() -> int:
        return 5

    assert fromargs.run(app, argv=["later"]) == 5


def test_async_handler_inside_running_loop_is_refused() -> None:
    ran: list[str] = []
    app = App()

    @app.command
    async def later() -> int:
        ran.append("later")
        return 5

    async def host() -> None:
        with pytest.raises(TypeError, match="running event loop"):
            fromargs.run(app, argv=["later"])

    asyncio.run(host())
    assert ran == []


def test_async_handler_on_other_backend_is_refused() -> None:
    ran: list[str] = []
    app = App(backend="trio")

    @app.command
    async def later() -> int:
        ran.append("later")
        return 5

    with pytest.raises(TypeError, match="asyncio backend, not 'trio'"):
        fromargs.run(app, argv=["later"])
    assert ran == []


def test_async_handler_on_nested_trio_backend_is_refused() -> None:
    ran: list[str] = []
    app = App()
    group = App(name="group", backend="trio")
    app.command(group)

    @group.command
    async def later() -> int:
        ran.append("later")
        return 5

    with pytest.raises(TypeError, match="asyncio backend, not 'trio'"):
        fromargs.run(app, argv=["group", "later"])
    assert ran == []


def test_nested_asyncio_backend_overrides_trio_root() -> None:
    ran: list[str] = []
    app = App(backend="trio")
    group = App(name="group", backend="asyncio")
    app.command(group)

    @group.command
    async def later() -> int:
        ran.append("later")
        return 5

    assert fromargs.run(app, argv=["group", "later"]) == 5
    assert ran == ["later"]


def test_str_argv_is_rejected() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    with pytest.raises(TypeError, match="not str"):
        fromargs.run(_app(calls), argv="show x")
    assert calls == []


def test_multiline_message_is_one_text_line(capsys: pytest.CaptureFixture[str]) -> None:
    app = App()

    @app.command
    def fail() -> None:
        raise fromargs.CliError("line one\nline two", exit_code=5)

    assert fromargs.run(app, argv=["fail"]) == 5
    assert capsys.readouterr().err == "ERROR: line one line two\n"
