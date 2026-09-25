"""Behavior of fromargs.App.run: dispatch, JSON output, and error rendering."""

# No `from __future__ import annotations`: Cyclopts resolves the Annotated
# hints of commands defined inside tests, which reference local converters.

import asyncio
import contextlib
import io
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

import pytest
from cyclopts import CycloptsError, Parameter, Token

import fromargs

JsonLine = Callable[[str], dict[str, object]]


def _unexpected_envelope(err: str) -> dict[str, object]:
    """Checker: ``err`` is exactly one three-key unexpected-exception envelope; returns it."""
    lines = err.splitlines()
    assert len(lines) == 1, err
    envelope = json.loads(lines[0])
    assert set(envelope) == {"error", "exit_code", "traceback"}
    return envelope


def _app(calls: list[tuple[str, dict[str, object]]]) -> fromargs.App:
    app = fromargs.App("t")

    @app.command
    def show(name: str, *, max_count: int = 1) -> dict[str, object]:
        calls.append(("show", {"name": name, "max_count": max_count}))
        return {"name": name, "max_count": max_count}

    @app.command
    def fail(kind: str) -> None:
        calls.append(("fail", {"kind": kind}))
        if kind == "contract":
            raise fromargs.contract_error(ValueError("bad shape"), context="load")
        raise fromargs.CliError("boom", exit_code=3)

    return app


def test_none_return_is_exit_0_with_no_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t")

    @app.command
    def noop() -> None:
        pass

    assert app.run(["noop"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_return_value_is_one_json_document(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    app = _app(calls)

    assert app.run(["show", "x"]) == 0
    assert json.loads(capsys.readouterr().out) == {"name": "x", "max_count": 1}
    assert calls == [("show", {"name": "x", "max_count": 1})]


def test_int_return_value_serializes_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t")

    @app.command
    def count() -> int:
        return 7

    assert app.run(["count"]) == 0
    assert capsys.readouterr().out == "7\n"


def test_stdout_parameter_captures_handler_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command
    def hello() -> None:
        print("hi")

    buffer = io.StringIO()
    assert app.run(["hello"], stdout=buffer) == 0
    assert buffer.getvalue() == "hi\n"
    assert capsys.readouterr().out == ""


def test_stdout_parameter_receives_the_json_result() -> None:
    app = fromargs.App("t")

    @app.command
    def greet() -> dict[str, str]:
        return {"hi": "there"}

    buffer = io.StringIO()
    assert app.run(["greet"], stdout=buffer) == 0
    assert json.loads(buffer.getvalue()) == {"hi": "there"}


def test_cli_error_json_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert _app(calls).run(["fail", "plain"]) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert single_json_line(captured.err) == {"error": "boom", "exit_code": 3}


def test_contract_error_json_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert _app(calls).run(["fail", "contract"]) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert single_json_line(captured.err) == {"error": "load: bad shape", "exit_code": 3}


def test_cli_error_default_exit_code_is_usage() -> None:
    assert fromargs.CliError("usage").exit_code == 2


def test_missing_command_is_a_json_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert _app(calls).run([]) == 2
    assert single_json_line(capsys.readouterr().err) == {
        "error": "command required",
        "exit_code": 2,
    }


def test_group_with_no_subcommand_is_a_json_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    app = fromargs.App("t")
    group = app.group("group")

    @group.command
    def sub() -> None:
        raise AssertionError("handler must not run")

    assert app.run(["group"]) == 2
    assert single_json_line(capsys.readouterr().err) == {
        "error": "command required",
        "exit_code": 2,
    }


def test_group_help_flag_still_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t")
    group = app.group("group")

    @group.command
    def sub() -> None:
        raise AssertionError("handler must not run")

    assert app.run(["group", "--help"]) == 0
    out, err = capsys.readouterr()
    assert out != ""
    assert err == ""


def test_did_you_mean_surfaces(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    app = _app(calls)

    assert app.run(["shw", "x"]) == 2
    assert "Did you mean" in capsys.readouterr().err

    assert app.run(["show", "x", "--max_cnt", "2"]) == 2
    assert "Did you mean --max-count?" in capsys.readouterr().err

    assert calls == []


def test_underscore_flag_binds() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    assert _app(calls).run(["show", "x", "--max_count", "3"]) == 0
    assert calls == [("show", {"name": "x", "max_count": 3})]


def _converter_app(converted: list[str]) -> fromargs.App:
    def checked(type_: object, tokens: Sequence[Token]) -> str:
        converted.append(tokens[0].value)
        if tokens[0].value == "bad":
            raise fromargs.CliError("custom bad value", exit_code=4)
        return tokens[0].value

    app = fromargs.App("t")

    @app.command
    def fetch(
        item: Annotated[str, Parameter(converter=checked)] = "", *, count: int = 0
    ) -> None:
        pass

    return app


def test_converter_cli_error_is_reported(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    converted: list[str] = []

    assert _converter_app(converted).run(["fetch", "bad"]) == 4
    assert single_json_line(capsys.readouterr().err) == {
        "error": "custom bad value",
        "exit_code": 4,
    }


def test_valid_argv_is_parsed_once() -> None:
    converted: list[str] = []

    assert _converter_app(converted).run(["fetch", "ok"]) == 0
    assert converted == ["ok"]


def test_converter_rejection_still_tries_repair(
    capsys: pytest.CaptureFixture[str],
) -> None:
    received: list[tuple[list[str], int]] = []

    def no_spaces(type_: object, tokens: Sequence[Token]) -> list[str]:
        values = [token.value for token in tokens]
        if any(" " in value for value in values):
            raise fromargs.CliError(f"bad {values}", exit_code=4)
        return values

    app = fromargs.App("t")

    @app.command
    def go(
        *, tags: Annotated[list[int], Parameter(converter=no_spaces)], limit: int = 0
    ) -> None:
        received.append((tags, limit))

    assert app.run(["go", "--tags", "a --limit 3"]) == 0
    assert received == [(["a"], 3)]
    assert "note: split quoted argument" in capsys.readouterr().err


def test_handler_cyclopts_error_is_an_unexpected_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command
    def inner() -> None:
        raise CycloptsError(msg="inner")

    assert app.run(["inner"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["error"] == "CycloptsError: inner"
    assert envelope["exit_code"] == 1
    assert Path(str(envelope["traceback"])).is_file()


def test_handler_value_error_is_an_unexpected_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command
    def inner() -> None:
        raise ValueError("bad shape")

    assert app.run(["inner"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["error"] == "ValueError: bad shape"
    assert envelope["exit_code"] == 1
    assert Path(str(envelope["traceback"])).is_file()


def test_handler_returning_nan_is_an_unexpected_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command
    def inner() -> float:
        return float("nan")

    assert app.run(["inner"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["exit_code"] == 1


def test_handler_returning_a_set_is_an_unexpected_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command
    def inner() -> set[int]:
        return {1, 2}

    assert app.run(["inner"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["exit_code"] == 1


def test_json_flag_is_a_noop_anywhere(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    app = _app(calls)

    assert app.run(["--json", "show", "x"]) == 0
    assert capsys.readouterr().err == ""
    assert app.run(["show", "--json", "x"]) == 0
    assert capsys.readouterr().err == ""
    assert calls == [
        ("show", {"name": "x", "max_count": 1}),
        ("show", {"name": "x", "max_count": 1}),
    ]


def test_full_flag_disables_truncation_anywhere(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t")

    @app.command(limit=1)
    def listing() -> list[int]:
        return [1, 2, 3]

    assert app.run(["--full", "listing"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == [1, 2, 3]
    assert err == ""


def test_json_after_double_dash_is_a_literal(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    app = fromargs.App("t")

    @app.command
    def echo(*words: str) -> None:
        raise fromargs.CliError(" ".join(words))

    assert app.run(["echo", "--", "--json"]) == 2
    assert single_json_line(capsys.readouterr().err) == {"error": "--json", "exit_code": 2}


def test_bool_return_value_serializes_as_json_true() -> None:
    app = fromargs.App("t")

    @app.command
    def yes() -> bool:
        return True

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert app.run(["yes"]) == 0
    assert buffer.getvalue() == "true\n"


def test_async_handler_is_awaited() -> None:
    app = fromargs.App("t")

    @app.command
    async def later() -> int:
        return 5

    buffer = io.StringIO()
    assert app.run(["later"], stdout=buffer) == 0
    assert buffer.getvalue() == "5\n"


def test_async_handler_inside_running_loop_is_refused() -> None:
    ran: list[str] = []
    app = fromargs.App("t")

    @app.command
    async def later() -> int:
        ran.append("later")
        return 5

    async def host() -> None:
        with pytest.raises(TypeError, match="running event loop"):
            app.run(["later"])

    asyncio.run(host())
    assert ran == []


def _root_trio_target() -> tuple[fromargs.App, fromargs.App, list[str]]:
    app = fromargs.App("t", backend="trio")
    return app, app, ["later"]


def _nested_trio_target() -> tuple[fromargs.App, fromargs.App, list[str]]:
    app = fromargs.App("t")
    group = app.group("group")
    group._cyclopts.backend = "trio"
    return app, group, ["group", "later"]


@pytest.mark.parametrize(
    "target_factory", [_root_trio_target, _nested_trio_target], ids=["root", "nested"]
)
def test_async_handler_on_trio_backend_is_refused(
    target_factory: Callable[[], tuple[fromargs.App, fromargs.App, list[str]]],
) -> None:
    ran: list[str] = []
    app, target, argv = target_factory()

    @target.command
    async def later() -> int:
        ran.append("later")
        return 5

    with pytest.raises(TypeError, match="asyncio backend, not 'trio'"):
        app.run(argv)
    assert ran == []


def test_nested_asyncio_backend_overrides_trio_root() -> None:
    ran: list[str] = []
    app = fromargs.App("t", backend="trio")
    group = app.group("group")
    group._cyclopts.backend = "asyncio"

    @group.command
    async def later() -> int:
        ran.append("later")
        return 5

    buffer = io.StringIO()
    assert app.run(["group", "later"], stdout=buffer) == 0
    assert ran == ["later"]


def test_str_argv_is_rejected() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    with pytest.raises(TypeError, match="not str"):
        _app(calls).run("show x")
    assert calls == []


def test_multiline_message_stays_one_stderr_line(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    app = fromargs.App("t")

    @app.command
    def fail() -> None:
        raise fromargs.CliError("line one\nline two", exit_code=5)

    assert app.run(["fail"]) == 5
    assert single_json_line(capsys.readouterr().err) == {
        "error": "line one\nline two",
        "exit_code": 5,
    }


def test_ambiguous_command_is_a_json_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    app = fromargs.App("t")

    @app.command
    def show_items() -> None:
        raise AssertionError("handler must not run")

    @app.command
    def showitems() -> None:
        raise AssertionError("handler must not run")

    assert app.run(["Show_Items"]) == 2
    envelope = single_json_line(capsys.readouterr().err)
    assert envelope["exit_code"] == 2
    assert "Ambiguous command" in str(envelope["error"])


def test_async_handler_cli_error_json_envelope(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    app = fromargs.App("t")

    @app.command
    async def later() -> int:
        raise fromargs.CliError("async bad", exit_code=6)

    assert app.run(["later"]) == 6
    assert single_json_line(capsys.readouterr().err) == {
        "error": "async bad",
        "exit_code": 6,
    }


def test_reserved_option_from_trailing_underscore_name_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--json'"):

        @app.command
        def bad(*, json_: bool = False) -> None:
            pass


def test_reserved_option_from_trailing_underscore_full_name_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--full'"):

        @app.command
        def bad(*, full_: bool = False) -> None:
            pass


def test_reserved_option_from_explicit_parameter_name_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--full'"):

        @app.command
        def bad(*, override: Annotated[bool, Parameter(name="--full")] = False) -> None:
            pass


def test_negative_limit_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="must not be negative"):

        @app.command(limit=-1)
        def listing() -> list[int]:
            return []


def test_limit_works_with_a_bound_method() -> None:
    class Lister:
        def items(self) -> list[int]:
            return [1, 2, 3]

    app = fromargs.App("t")
    app.command(Lister().items, name="items", limit=1)

    buffer = io.StringIO()
    assert app.run(["items"], stdout=buffer) == 0
    assert json.loads(buffer.getvalue()) == [1]


def test_one_function_under_two_names_keeps_independent_limits() -> None:
    app = fromargs.App("t")

    def items() -> list[int]:
        return [1, 2, 3]

    app.command(items, name="short", limit=1)
    app.command(items, name="long", limit=2)

    short_buffer = io.StringIO()
    assert app.run(["short"], stdout=short_buffer) == 0
    assert json.loads(short_buffer.getvalue()) == [1]

    long_buffer = io.StringIO()
    assert app.run(["long"], stdout=long_buffer) == 0
    assert json.loads(long_buffer.getvalue()) == [1, 2]
