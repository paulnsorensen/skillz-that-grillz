"""Behavior of fromargs.App.run: dispatch, JSON output, and error rendering."""

# No `from __future__ import annotations`: Cyclopts resolves the Annotated
# hints of commands defined inside tests, which reference local converters.

import asyncio
import contextlib
import io
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, cast

import pytest
from cyclopts import CycloptsError, Parameter, Token

import fromargs
from fromargs._errors import InvalidExitCodeError

JsonLine = Callable[[str], dict[str, object]]


def _unexpected_envelope(err: str) -> dict[str, object]:
    """Checker: ``err`` is exactly one three-key unexpected-exception envelope; returns it."""
    lines = err.splitlines()
    assert len(lines) == 1, err
    envelope = cast("dict[str, object]", json.loads(lines[0]))
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
    def checked(_type_: object, tokens: Sequence[Token]) -> str:
        converted.append(tokens[0].value)
        if tokens[0].value == "bad":
            raise fromargs.CliError("custom bad value", exit_code=4)
        return tokens[0].value

    app = fromargs.App("t")

    @app.command
    def fetch(
        _item: Annotated[str, Parameter(converter=checked)] = "", *, _count: int = 0
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
    received: list[tuple[list[int], int]] = []

    def no_spaces(_type_: object, tokens: Sequence[Token]) -> list[str]:
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
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    app = fromargs.App("t")

    @app.command
    def inner() -> None:
        raise CycloptsError(msg="inner")

    assert app.run(["inner"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["error"] == "CycloptsError: inner"
    assert envelope["exit_code"] == 1
    traceback_path = Path(str(envelope["traceback"]))
    assert traceback_path.parent == tmp_path
    assert "CycloptsError" in traceback_path.read_text()


def test_handler_value_error_is_an_unexpected_envelope(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    app = fromargs.App("t")

    @app.command
    def inner() -> None:
        raise ValueError("bad shape")

    assert app.run(["inner"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["error"] == "ValueError: bad shape"
    assert envelope["exit_code"] == 1
    traceback_path = Path(str(envelope["traceback"]))
    assert traceback_path.parent == tmp_path
    assert "bad shape" in traceback_path.read_text()


def test_traceback_write_failure_still_reports_an_envelope_without_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, single_json_line: JsonLine
) -> None:
    def _broken_mkstemp(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise OSError("no space left on device")

    monkeypatch.setattr(tempfile, "mkstemp", _broken_mkstemp)
    app = fromargs.App("t")

    @app.command
    def inner() -> None:
        raise ValueError("bad shape")

    assert app.run(["inner"]) == 1
    envelope = single_json_line(capsys.readouterr().err)
    assert envelope == {"error": "ValueError: bad shape", "exit_code": 1}


def test_traceback_write_failure_removes_the_partial_file(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    single_json_line: JsonLine,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def _broken_open(*_args: object, **_kwargs: object) -> object:
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _broken_open)
    app = fromargs.App("t")

    @app.command
    def inner() -> None:
        raise ValueError("bad shape")

    assert app.run(["inner"]) == 1
    envelope = single_json_line(capsys.readouterr().err)
    assert envelope == {"error": "ValueError: bad shape", "exit_code": 1}
    assert list(tmp_path.glob("fromargs-*.traceback")) == []


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


def test_handler_returning_bytes_is_an_unexpected_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command
    def inner() -> bytes:
        return b"x"

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
            _ = app.run(["later"])

    asyncio.run(host())
    assert ran == []


def _root_trio_target() -> tuple[fromargs.App, fromargs.App, list[str]]:
    app = fromargs.App("t", backend="trio")
    return app, app, ["later"]


def _nested_trio_target() -> tuple[fromargs.App, fromargs.App, list[str]]:
    app = fromargs.App("t")
    group = app.group("group")
    group._cyclopts.backend = "trio"  # pyright: ignore[reportPrivateUsage] -- App exposes no public backend setter for a nested group
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
        _ = app.run(argv)
    assert ran == []


def test_nested_asyncio_backend_overrides_trio_root() -> None:
    ran: list[str] = []
    app = fromargs.App("t", backend="trio")
    group = app.group("group")
    group._cyclopts.backend = "asyncio"  # pyright: ignore[reportPrivateUsage] -- App exposes no public backend setter for a nested group

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
        _ = _app(calls).run("show x")
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


def test_near_miss_command_is_not_fuzzy_matched(
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
    assert "Unknown command" in str(envelope["error"])
    assert "Did you mean" in str(envelope["error"])


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


@pytest.mark.parametrize("exit_code", [0, 1, -1, 256, 1000])
def test_cli_error_rejects_out_of_range_exit_code(exit_code: int) -> None:
    with pytest.raises(InvalidExitCodeError, match="exit_code"):
        _ = fromargs.CliError("x", exit_code=exit_code)


def test_invalid_exit_code_in_converter_is_unexpected_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    def conv(_type: object, _tokens: object) -> str:
        raise fromargs.CliError("bad", exit_code=1)

    @app.command
    def go(x: Annotated[str, fromargs.Parameter(converter=conv)]) -> str:
        return x

    assert app.run(["go", "a"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["exit_code"] == 1
    assert "exit_code" in str(envelope["error"])


def test_invalid_exit_code_in_validator_is_unexpected_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    def check(_type: object, _value: object) -> None:
        raise fromargs.CliError("bad", exit_code=1)

    @app.command
    def go(x: Annotated[str, fromargs.Parameter(validator=check)]) -> str:
        return x

    assert app.run(["go", "a"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["exit_code"] == 1
    assert "exit_code" in str(envelope["error"])


@pytest.mark.parametrize("exit_code", [True, False, 2.0, "3", None])
def test_cli_error_rejects_non_int_exit_code(exit_code: object) -> None:
    with pytest.raises(InvalidExitCodeError, match="exit_code"):
        _ = fromargs.CliError("x", exit_code=cast("int", exit_code))


@pytest.mark.parametrize("exit_code", [2, 3, 255])
def test_cli_error_accepts_exit_code_bounds(exit_code: int) -> None:
    assert fromargs.CliError("x", exit_code=exit_code).exit_code == exit_code


def test_reserved_option_from_trailing_underscore_name_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--json'"):

        @app.command
        def bad(*, _json_: bool = False) -> None:
            pass


def test_reserved_option_from_trailing_underscore_full_name_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--full'"):

        @app.command
        def bad(*, _full_: bool = False) -> None:
            pass


def test_reserved_option_from_explicit_parameter_name_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--full'"):

        @app.command
        def bad(*, _override: Annotated[bool, Parameter(name="--full")] = False) -> None:
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
    _ = app.command(Lister().items, name="items", limit=1)

    buffer = io.StringIO()
    assert app.run(["items"], stdout=buffer) == 0
    assert json.loads(buffer.getvalue()) == [1]


def test_one_function_under_two_names_keeps_independent_limits() -> None:
    app = fromargs.App("t")

    def items() -> list[int]:
        return [1, 2, 3]

    _ = app.command(items, name="short", limit=1)
    _ = app.command(items, name="long", limit=2)

    short_buffer = io.StringIO()
    assert app.run(["short"], stdout=short_buffer) == 0
    assert json.loads(short_buffer.getvalue()) == [1]

    long_buffer = io.StringIO()
    assert app.run(["long"], stdout=long_buffer) == 0
    assert json.loads(long_buffer.getvalue()) == [1, 2]


def _raising_app(exc: BaseException, *, is_async: bool = False) -> fromargs.App:
    app = fromargs.App("t")

    if is_async:

        @app.command(name="leave")
        async def leave_async() -> None:
            raise exc

    else:

        @app.command(name="leave")
        def leave_sync() -> None:
            raise exc

    return app


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    ("code", "status"), [(4, 4), (True, 1), ("bad input", 1), (object, 1), (0.0, 1)]
)
def test_system_exit_failure_is_an_envelope_with_that_status(
    capsys: pytest.CaptureFixture[str], is_async: bool, code: object, status: int
) -> None:
    app = _raising_app(SystemExit(code), is_async=is_async)

    assert app.run(["leave"]) == status
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    envelope = cast("dict[str, object]", json.loads(lines[0]))
    assert envelope == {"error": envelope["error"], "exit_code": status}
    assert type(envelope["exit_code"]) is int
    if isinstance(code, str):
        assert envelope["error"] == code


@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("code", [0, None])
def test_system_exit_success_returns_zero_silently(
    capsys: pytest.CaptureFixture[str], is_async: bool, code: int | None
) -> None:
    app = _raising_app(SystemExit(code), is_async=is_async)

    assert app.run(["leave"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("code", [256, -1])
def test_system_exit_out_of_range_int_reports_original_code_at_exit_1(
    capsys: pytest.CaptureFixture[str], code: int
) -> None:
    app = _raising_app(SystemExit(code))

    assert app.run(["leave"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": f"exited with status {code}", "exit_code": 1}


def test_converter_keyboard_interrupt_is_reported_at_130(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def convert(_type: object, _tokens: object) -> int:
        raise KeyboardInterrupt

    app = fromargs.App("t")

    @app.command
    def cmd(value: Annotated[int, Parameter(converter=convert)]) -> int:
        return value

    assert app.run(["cmd", "1"]) == 130
    assert json.loads(capsys.readouterr().err) == {"error": "interrupted", "exit_code": 130}


def test_converter_system_exit_sets_the_status(capsys: pytest.CaptureFixture[str]) -> None:
    def convert(_type: object, _tokens: object) -> int:
        sys.exit(3)

    app = fromargs.App("t")

    @app.command
    def cmd(value: Annotated[int, Parameter(converter=convert)]) -> int:
        return value

    assert app.run(["cmd", "1"]) == 3
    assert json.loads(capsys.readouterr().err) == {"error": "exited with status 3", "exit_code": 3}


@pytest.mark.parametrize("is_async", [False, True])
def test_keyboard_interrupt_is_an_exit_130_envelope(
    capsys: pytest.CaptureFixture[str], is_async: bool
) -> None:
    app = _raising_app(KeyboardInterrupt(), is_async=is_async)

    assert app.run(["leave"]) == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "interrupted", "exit_code": 130}


def test_generator_exit_still_propagates() -> None:
    app = _raising_app(GeneratorExit())

    with pytest.raises(GeneratorExit):
        _ = app.run(["leave"])


def test_validator_unexpected_exception_is_an_unexpected_envelope(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def boom(_type_: object, _value: object) -> None:
        raise RuntimeError("validator boom")

    app = fromargs.App("t")

    @app.command
    def v(x: Annotated[int, Parameter(validator=boom)]) -> int:
        return x

    assert app.run(["v", "1"]) == 1
    envelope = _unexpected_envelope(capsys.readouterr().err)
    assert envelope["error"] == "RuntimeError: validator boom"
    assert envelope["exit_code"] == 1
    assert "validator boom" in Path(str(envelope["traceback"])).read_text()


def test_converter_clierror_keeps_its_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    def reject(_type_: object, _tokens: Sequence[Token]) -> str:
        raise fromargs.CliError("nope", exit_code=4)

    app = fromargs.App("t")

    @app.command
    def go(x: Annotated[str, Parameter(converter=reject)]) -> str:
        return x

    assert app.run(["go", "a"]) == 4
    assert json.loads(capsys.readouterr().err) == {"error": "nope", "exit_code": 4}


def test_repair_probe_crash_rejects_the_candidate(capsys: pytest.CaptureFixture[str]) -> None:
    # ``list[int]`` is splittable, so the repair probes ``--ids 1 --limit 3``.
    def no_spaces(_type_: object, tokens: Sequence[Token]) -> list[int]:
        values = [token.value for token in tokens]
        if any(" " in value for value in values):
            raise fromargs.CliError("has space", exit_code=4)
        return [int(value) for value in values]

    def crash(_type_: object, _value: object) -> None:
        raise RuntimeError("probe boom")

    app = fromargs.App("t")

    @app.command
    def go(
        *,
        ids: Annotated[list[int], Parameter(converter=no_spaces, validator=crash)],
        limit: int = 0,
    ) -> list[int]:
        return [*ids, limit]

    assert app.run(["go", "--ids", "1 --limit 3"]) == 4
    assert json.loads(capsys.readouterr().err) == {"error": "has space", "exit_code": 4}
