"""Behavior of the argv repairs: leading-flag hoist and quote-split probing."""

# No `from __future__ import annotations`: Cyclopts resolves the Annotated
# hints of commands defined inside tests, which reference local converters.

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

import pytest
from cyclopts import App, Parameter, Token, validators

import fromargs
from fromargs._argv import hoist_leading_flags

JsonLine = Callable[[str], dict[str, object]]


def _app(calls: list[dict[str, object]]) -> App:
    app = App()

    @app.command
    def show(
        name: str,
        *,
        json: bool = False,
        full: bool = False,
        count: int = 1,
        tags: list[str] | None = None,
        label: str = "",
    ) -> None:
        calls.append(
            {
                "name": name,
                "json": json,
                "full": full,
                "count": count,
                "tags": tags,
                "label": label,
            }
        )

    return app


def test_hoists_leading_json(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=["--json", "show", "x", "--count", "2"]) == 0

    assert calls == [
        {
            "name": "x",
            "json": True,
            "full": False,
            "count": 2,
            "tags": None,
            "label": "",
        }
    ]
    assert capsys.readouterr().err == "note: moved --json after 'show'\n"


def test_hoists_leading_json_and_full(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=["--full", "--json", "show", "x"]) == 0

    assert calls[0]["json"] is True
    assert calls[0]["full"] is True
    assert capsys.readouterr().err == "note: moved --full --json after 'show'\n"


@pytest.mark.parametrize(
    ("argv", "rejected"),
    [
        (["--count", "show", "x"], "--count"),
        (["--json", "--count", "show", "x"], "--json"),
    ],
)
def test_other_leading_flag_passes_through_unchanged(
    capsys: pytest.CaptureFixture[str], argv: list[str], rejected: str
) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=argv) == 2

    err = capsys.readouterr().err
    assert "note:" not in err
    assert f"Unknown command {rejected}" in err.replace('"', "").replace("\\", "")
    assert calls == []


def test_leading_flag_before_unknown_command_keeps_suggestion(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[dict[str, object]] = []

    for argv in (["--json", "shw", "x"], ["--json", "shw"]):
        assert fromargs.run(_app(calls), argv=argv) == 2
        err = capsys.readouterr().err
        assert "note:" not in err
        assert "Did you mean" in str(single_json_line(err)["error"])
    assert calls == []


def test_leading_flag_before_unknown_nested_command_keeps_suggestion(
    capsys: pytest.CaptureFixture[str], single_json_line: JsonLine
) -> None:
    calls: list[str] = []
    app = App()
    group = App(name="grp")
    app.command(group)

    @group.command
    def deep(*, json: bool = False) -> None:
        calls.append("deep")

    assert fromargs.run(app, argv=["--json", "grp", "dep"]) == 2
    err = capsys.readouterr().err
    assert "note:" not in err
    assert "Did you mean" in str(single_json_line(err)["error"])
    assert calls == []


def test_hoists_after_nested_command(capsys: pytest.CaptureFixture[str]) -> None:
    received: list[tuple[str, bool]] = []
    app = App()
    group = App(name="group")
    app.command(group)

    @group.command
    def leaf(name: str, *, json: bool = False) -> None:
        received.append((name, json))

    assert fromargs.run(app, argv=["--json", "group", "leaf", "x"]) == 0

    assert received == [("x", True)]
    assert capsys.readouterr().err == "note: moved --json after 'group leaf'\n"


def test_splits_single_verified_candidate(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []
    app = _app(calls)

    repaired = fromargs.repair_argv(app, ["show", "x", "--count", "2 --full"])

    assert repaired == ["show", "x", "--count", "2", "--full"]
    assert (
        capsys.readouterr().err
        == "note: split quoted argument '2 --full' into ['2', '--full']\n"
    )
    assert calls == []


def test_splits_equals_form(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []

    repaired = fromargs.repair_argv(_app(calls), ["show", "x", "--count=2 --full"])

    assert repaired == ["show", "x", "--count=2", "--full"]
    assert "note: split quoted argument" in capsys.readouterr().err


def test_run_uses_the_split(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []

    assert fromargs.run(_app(calls), argv=["show", "x", "--count", "2 --full"]) == 0

    assert calls == [
        {
            "name": "x",
            "json": False,
            "full": True,
            "count": 2,
            "tags": None,
            "label": "",
        }
    ]
    assert "note: split quoted argument" in capsys.readouterr().err


def test_valid_argv_is_returned_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []
    argv = ["show", "x", "--tags", "a --count 2"]

    assert fromargs.repair_argv(_app(calls), argv) == argv
    assert capsys.readouterr().err == ""


def _lenient(type_: object, tokens: Sequence[Token]) -> int:
    """Accept any value, so a merged and a split token both parse."""
    return 0


# `Lenient` options are splittable (not free text) yet accept a merged value,
# so each of two merged tokens yields a candidate that parses.
Lenient = Annotated[int, Parameter(converter=_lenient)]


def test_ambiguous_candidates_keep_argv(capsys: pytest.CaptureFixture[str]) -> None:
    app = App()

    @app.command
    def tag(*, first: Lenient, second: Lenient, count: int) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["tag", "--first", "a --count 1", "--second", "b --count 2"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_probing_stops_at_second_candidate(capsys: pytest.CaptureFixture[str]) -> None:
    converted: list[str] = []

    def record(type_: object, tokens: Sequence[Token]) -> int:
        converted.extend(token.value for token in tokens)
        return 0

    app = App()

    @app.command
    def tag(
        *,
        first: Lenient,
        second: Lenient,
        count: int,
        mark: Annotated[int, Parameter(converter=record)],
    ) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["tag", "--first", "a --count 1", "--second", "b --count 2"]
    argv += ["--mark", "m --count 3"]

    assert fromargs.repair_argv(app, argv) == argv
    assert "m" not in converted
    assert capsys.readouterr().err == ""


def test_zero_candidates_keep_argv(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []
    argv = ["show", "x", "--count", "two --full"]

    assert fromargs.repair_argv(_app(calls), argv) == argv
    assert capsys.readouterr().err == ""


def test_plain_str_option_value_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def note(*, label: str, count: int) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["note", "--label", "a --count 2"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_token_after_double_dash_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def triple(first: str, second: str, third: str, *, count: int = 0) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["triple", "--", "--count", "x -y"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_value_after_boolean_flag_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def note(text: str, *, verbose: bool = False, count: int) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["note", "--verbose", "hello --count 2"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_help_flag_inside_pieces_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []
    argv = ["show", "x", "--count", "2 --help"]

    assert fromargs.repair_argv(_app(calls), argv) == argv
    assert capsys.readouterr().err == ""


def test_app_help_flags_guard_the_split(capsys: pytest.CaptureFixture[str]) -> None:
    app = App(help_flags=["--usage"])

    @app.command
    def fetch(*, tags: list[int]) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["fetch", "--tags", "1 --usage"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_split_without_dash_piece_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def pair(*, point: tuple[int, int]) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["pair", "--point", "1 2"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_path_option_value_is_not_split(capsys: pytest.CaptureFixture[str]) -> None:
    app = App()

    @app.command
    def p(
        *,
        path: Annotated[Path, Parameter(validator=validators.Path(exists=True))],
        force: bool = False,
    ) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["p", "--path", "pyproject.toml --force"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_list_str_option_value_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def label(*, tags: list[str], target: str, force: bool = False) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["label", "--tags", "x --target /etc --force"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_optional_str_option_value_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def note(*, label: str | None = None, count: int) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["note", "--label", "a --count 2"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_split_refused_when_pieces_contain_version_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def show(*, count: int) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["show", "--count", "2 --version"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_split_refused_when_pieces_contain_nested_help_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()
    group = App(name="grp", help_flags=["--usage"])
    app.command(group)

    @group.command
    def fetch(*, tags: list[int]) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["grp", "fetch", "--tags", "1 --usage"]

    assert fromargs.repair_argv(app, argv) == argv
    assert capsys.readouterr().err == ""


def test_hoist_does_not_misplace_flags_past_a_gap_in_the_command() -> None:
    app = App()

    @app.meta.default
    def meta(
        *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
        verbose: bool = False,
    ) -> object:
        return app(tokens)

    db = App(name="db")
    app.command(db)

    @db.command
    def migrate(target: str = "head", *, json: bool = False) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["--json", "db", "--verbose", "migrate"]

    assert hoist_leading_flags(app.meta, argv) == [
        "db",
        "--json",
        "--verbose",
        "migrate",
    ]
