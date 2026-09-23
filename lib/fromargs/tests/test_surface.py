"""Public surface of fromargs and native Cyclopts JSON-string parameters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cyclopts import App

import fromargs


@dataclass
class Point:
    a: int


def test_public_surface() -> None:
    assert set(fromargs.__all__) == {
        "CliError",
        "contract_error",
        "emit",
        "repair_argv",
        "run",
    }
    assert len(fromargs.__all__) == 5
    assert Path(fromargs.__file__).with_name("py.typed").is_file()
    for name in fromargs.__all__:
        assert hasattr(fromargs, name)


def test_json_string_parameters_still_parse() -> None:
    received: list[object] = []
    app = App()

    @app.command
    def conf(*, point: Point, numbers: list[int]) -> None:
        received.extend([point, numbers])

    assert (
        fromargs.run(app, argv=["conf", "--point", '{"a": 1}', "--numbers", "[1, 2]"])
        == 0
    )
    assert received == [Point(a=1), [1, 2]]
