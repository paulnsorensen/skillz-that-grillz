"""Public surface of fromargs: App decorators, groups, and reserved parameters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import fromargs


@dataclass
class Point:
    a: int


def test_public_surface() -> None:
    assert set(fromargs.__all__) == {"App", "CliError", "contract_error"}
    assert len(fromargs.__all__) == 3
    assert Path(fromargs.__file__).with_name("py.typed").is_file()
    for name in fromargs.__all__:
        assert hasattr(fromargs, name)


def test_bare_decorator_registers_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t")
    calls: list[str] = []

    @app.command
    def greet(name: str) -> str:
        calls.append(name)
        return f"hi {name}"

    assert app.run(["greet", "x"]) == 0
    assert calls == ["x"]
    assert json.loads(capsys.readouterr().out) == "hi x"


def test_decorator_with_name_and_limit_registers_a_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command(name="ls", limit=1)
    def listing() -> list[int]:
        return [1, 2]

    assert app.run(["ls"]) == 0
    assert "note: showing 1 of 2" in capsys.readouterr().err


def test_group_returns_a_nested_command_registrar(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t")
    group = app.group("wheels", help="Inspect wheels.")
    calls: list[str] = []

    @group.command(name="list")
    def listing() -> list[str]:
        calls.append("listing")
        return ["a"]

    assert app.run(["wheels", "list"]) == 0
    assert calls == ["listing"]
    assert json.loads(capsys.readouterr().out) == ["a"]


def test_group_nests_further_groups() -> None:
    app = fromargs.App("t")
    outer = app.group("a")
    inner = outer.group("b")
    calls: list[str] = []

    @inner.command
    def deep() -> None:
        calls.append("deep")

    assert app.run(["a", "b", "deep"]) == 0
    assert calls == ["deep"]


@pytest.mark.parametrize("reserved", ["json", "full"])
def test_reserved_parameter_is_rejected_at_registration(reserved: str) -> None:
    app = fromargs.App("t")

    def register_json() -> None:
        @app.command
        def bad(*, json: bool = False) -> None:
            pass

    def register_full() -> None:
        @app.command
        def bad(*, full: bool = False) -> None:
            pass

    register = {"json": register_json, "full": register_full}[reserved]
    with pytest.raises(ValueError, match=reserved):
        register()


def test_json_string_parameters_still_parse() -> None:
    received: list[object] = []
    app = fromargs.App("t")

    @app.command
    def conf(*, point: Point, numbers: list[int]) -> None:
        received.extend([point, numbers])

    assert app.run(["conf", "--point", '{"a": 1}', "--numbers", "[1, 2]"]) == 0
    assert received == [Point(a=1), [1, 2]]
