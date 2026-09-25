"""Behavior of the private argv repairs: global-flag stripping and quote-split probing."""

# No `from __future__ import annotations`: Cyclopts resolves the Annotated
# hints of commands defined inside tests, which reference local converters.

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import pytest
from cyclopts import App, Parameter, Token, validators

import fromargs
from fromargs._argv import repair_rejected, strip_global_flags


def _app(calls: list[dict[str, object]]) -> App:
    app = App()

    @app.command
    def show(
        name: str,
        *,
        count: int = 1,
        tags: list[str] | None = None,
        label: str = "",
    ) -> None:
        calls.append({"name": name, "count": count, "tags": tags, "label": label})

    return app


def test_strip_removes_bare_json_and_full_anywhere() -> None:
    calls: list[dict[str, object]] = []
    app = _app(calls)

    tokens, full = strip_global_flags(app, ["--json", "show", "x", "--full", "--count", "2"])

    assert tokens == ["show", "x", "--count", "2"]
    assert full is True


def test_strip_without_full_reports_false() -> None:
    app = _app([])

    tokens, full = strip_global_flags(app, ["--json", "show", "x"])

    assert tokens == ["show", "x"]
    assert full is False


def test_strip_leaves_tokens_after_the_delimiter_untouched() -> None:
    app = App()

    @app.command
    def echo(*words: str) -> None:  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option.
        pass

    tokens, full = strip_global_flags(app, ["echo", "--", "--json", "--full"])

    assert tokens == ["echo", "--", "--json", "--full"]
    assert full is False


def test_splits_single_verified_candidate(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []
    app = _app(calls)

    repaired = repair_rejected(app, ["show", "x", "--count", "2 --label y"])

    assert repaired == ["show", "x", "--count", "2", "--label", "y"]
    assert (
        capsys.readouterr().err
        == "note: split quoted argument '2 --label y' into ['2', '--label', 'y']\n"
    )
    assert calls == []


def test_splits_equals_form(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []

    repaired = repair_rejected(_app(calls), ["show", "x", "--count=2 --label y"])

    assert repaired == ["show", "x", "--count=2", "--label", "y"]
    assert "note: split quoted argument" in capsys.readouterr().err


def test_valid_argv_is_returned_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []
    app = fromargs.App("t")

    @app.command
    def show(
        name: str,
        *,
        count: int = 1,
        tags: list[str] | None = None,
        label: str = "",
    ) -> None:
        calls.append({"name": name, "count": count, "tags": tags, "label": label})

    argv = ["show", "x", "--tags", "a --count 2"]

    assert app.run(argv) == 0
    assert calls == [{"name": "x", "count": 1, "tags": ["a --count 2"], "label": ""}]
    assert capsys.readouterr().err == ""


def _lenient(type_: object, tokens: Sequence[Token]) -> int:  # pyright: ignore[reportUnusedParameter] -- converter signature, not used by body.
    """Accept any value, so a merged and a split token both parse."""
    return 0


# `Lenient` options are splittable (not free text) yet accept a merged value,
# so each of two merged tokens yields a candidate that parses.
Lenient = Annotated[int, Parameter(converter=_lenient)]


def test_ambiguous_candidates_keep_argv(capsys: pytest.CaptureFixture[str]) -> None:
    app = App()

    @app.command
    def tag(
        *, first: Lenient, second: Lenient, count: int  # pyright: ignore[reportUnusedParameter] -- names bind Cyclopts options; probing never calls this.
    ) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["tag", "--first", "a --count 1", "--second", "b --count 2"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_probing_stops_at_second_candidate(capsys: pytest.CaptureFixture[str]) -> None:
    converted: list[str] = []

    def record(
        type_: object, tokens: Sequence[Token]  # pyright: ignore[reportUnusedParameter] -- converter signature, not used by body.
    ) -> int:
        converted.extend(token.value for token in tokens)
        return 0

    app = App()

    @app.command
    def tag(
        *,
        first: Lenient,  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
        second: Lenient,  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
        count: int,  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
        mark: Annotated[  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
            int, Parameter(converter=record)
        ],
    ) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["tag", "--first", "a --count 1", "--second", "b --count 2"]
    argv += ["--mark", "m --count 3"]

    assert repair_rejected(app, argv) is None
    assert "m" not in converted
    assert capsys.readouterr().err == ""


def test_zero_candidates_keep_argv(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[dict[str, object]] = []
    argv = ["show", "x", "--count", "two --label y"]

    assert repair_rejected(_app(calls), argv) is None
    assert capsys.readouterr().err == ""


def test_plain_str_option_value_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def note(*, label: str, count: int) -> None:  # pyright: ignore[reportUnusedParameter] -- names bind Cyclopts options; probing never calls this.
        raise AssertionError("probing must not run a handler")

    argv = ["note", "--label", "a --count 2"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_token_after_double_dash_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def triple(
        first: str, second: str, third: str, *, count: int = 0  # pyright: ignore[reportUnusedParameter] -- names bind Cyclopts options; probing never calls this.
    ) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["triple", "--", "--count", "x -y"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_value_after_boolean_flag_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def note(
        text: str, *, verbose: bool = False, count: int  # pyright: ignore[reportUnusedParameter] -- names bind Cyclopts options; probing never calls this.
    ) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["note", "--verbose", "hello --count 2"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_help_flag_inside_pieces_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[dict[str, object]] = []
    argv = ["show", "x", "--count", "2 --help"]

    assert repair_rejected(_app(calls), argv) is None
    assert capsys.readouterr().err == ""


def test_app_help_flags_guard_the_split(capsys: pytest.CaptureFixture[str]) -> None:
    app = App(help_flags=["--usage"])

    @app.command
    def fetch(*, tags: list[int]) -> None:  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
        raise AssertionError("probing must not run a handler")

    argv = ["fetch", "--tags", "1 --usage"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_split_without_dash_piece_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def pair(*, point: tuple[int, int]) -> None:  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
        raise AssertionError("probing must not run a handler")

    argv = ["pair", "--point", "1 2"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_path_option_value_is_not_split(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    app = App()

    @app.command
    def p(
        *,
        path: Annotated[  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
            Path, Parameter(validator=validators.Path(exists=True))
        ],
        force: bool = False,  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
    ) -> None:
        raise AssertionError("probing must not run a handler")

    target = tmp_path / "wheel.toml"
    _ = target.write_text("")
    argv = ["p", "--path", f"{target} --force"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_list_str_option_value_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def label(
        *, tags: list[str], target: str, force: bool = False  # pyright: ignore[reportUnusedParameter] -- names bind Cyclopts options; probing never calls this.
    ) -> None:
        raise AssertionError("probing must not run a handler")

    argv = ["label", "--tags", "x --target /etc --force"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_optional_str_option_value_is_not_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def note(*, label: str | None = None, count: int) -> None:  # pyright: ignore[reportUnusedParameter] -- names bind Cyclopts options; probing never calls this.
        raise AssertionError("probing must not run a handler")

    argv = ["note", "--label", "a --count 2"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_split_refused_when_pieces_contain_version_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()

    @app.command
    def show(*, count: int) -> None:  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
        raise AssertionError("probing must not run a handler")

    argv = ["show", "--count", "2 --version"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""


def test_split_refused_when_pieces_contain_nested_help_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = App()
    group = App(name="grp", help_flags=["--usage"])
    _ = app.command(group)

    @group.command
    def fetch(*, tags: list[int]) -> None:  # pyright: ignore[reportUnusedParameter] -- name binds a Cyclopts option; probing never calls this.
        raise AssertionError("probing must not run a handler")

    argv = ["grp", "fetch", "--tags", "1 --usage"]

    assert repair_rejected(app, argv) is None
    assert capsys.readouterr().err == ""
