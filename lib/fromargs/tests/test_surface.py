"""Public surface of fromargs: App decorators, groups, and reserved parameters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cyclopts
import pytest

import fromargs


@dataclass
class Point:
    a: int


def test_public_surface() -> None:
    assert set(fromargs.__all__) == {"App", "CliError", "contract_error", "Group", "Parameter"}
    assert len(fromargs.__all__) == 5
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


def test_command_with_limit_and_a_custom_name_transform_keeps_the_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command(limit=1, name_transform=str.upper)
    def listing() -> list[int]:
        return [1, 2]

    assert app.run(["LISTING"]) == 0
    assert "note: showing 1 of 2" in capsys.readouterr().err


def test_command_with_two_names_and_a_limit_truncates_under_either_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command(name=["ls", "list"], limit=1)
    def listing() -> list[int]:
        return [1, 2]

    assert app.run(["ls"]) == 0
    assert "note: showing 1 of 2" in capsys.readouterr().err
    assert app.run(["list"]) == 0
    assert "note: showing 1 of 2" in capsys.readouterr().err


def test_command_with_an_alias_and_a_limit_truncates_under_either_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.command(alias="ls", limit=1)
    def listing() -> list[int]:
        return [1, 2]

    assert app.run(["listing"]) == 0
    assert "note: showing 1 of 2" in capsys.readouterr().err
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


def test_group_passes_extra_kwargs_to_the_nested_cyclopts_app(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")
    _ = app.group("sub", version="9.9.9")

    assert app.run(["sub", "--version"]) == 0
    assert capsys.readouterr().out.strip() == "9.9.9"


def test_bare_default_registers_the_no_subcommand_handler() -> None:
    app = fromargs.App("t")

    @app.default
    def main() -> str:
        return "ran"

    assert app.run([]) == 0


def test_called_default_with_limit_truncates_its_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    @app.default(limit=1)
    def main() -> list[int]:
        return [1, 2]

    assert app.run([]) == 0
    assert "note: showing 1 of 2" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["command", "default"])
@pytest.mark.parametrize("reserved", ["json", "full"])
def test_reserved_parameter_is_rejected_at_registration(path: str, reserved: str) -> None:
    app = fromargs.App("t")

    def existing_validator(**_kwargs: object) -> None:
        return None

    before_commands = set(app._cyclopts)  # pyright: ignore[reportPrivateUsage] -- App exposes no public command listing
    before_default_command = app._cyclopts.default_command  # pyright: ignore[reportPrivateUsage] -- App exposes no public default_command getter
    before_validator = app._cyclopts.validator  # pyright: ignore[reportPrivateUsage] -- App exposes no public validator getter

    def register_command_json() -> None:
        @app.command
        def bad(*, _json: bool = False) -> None:
            pass

    def register_command_full() -> None:
        @app.command
        def bad(*, _full: bool = False) -> None:
            pass

    def register_default_json() -> None:
        @app.default(validator=existing_validator)
        def bad(*, _json: bool = False) -> None:
            pass

    def register_default_full() -> None:
        @app.default(validator=existing_validator)
        def bad(*, _full: bool = False) -> None:
            pass

    register = {
        ("command", "json"): register_command_json,
        ("command", "full"): register_command_full,
        ("default", "json"): register_default_json,
        ("default", "full"): register_default_full,
    }[(path, reserved)]
    with pytest.raises(ValueError, match=reserved):
        register()

    assert set(app._cyclopts) == before_commands  # pyright: ignore[reportPrivateUsage] -- App exposes no public command listing
    assert app._cyclopts.default_command == before_default_command  # pyright: ignore[reportPrivateUsage] -- App exposes no public default_command getter
    assert app._cyclopts.validator == before_validator  # pyright: ignore[reportPrivateUsage] -- App exposes no public validator getter


def test_reserved_option_via_inherited_default_parameter_is_rejected() -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--json'"):

        @app.command(default_parameter=cyclopts.Parameter(name="--json"))
        def bad(*, _weird: bool = False) -> None:
            pass


def test_option_renamed_away_from_reserved_by_default_parameter_is_allowed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")
    calls: list[bool] = []

    @app.command(default_parameter=cyclopts.Parameter(name="--json-x"))
    def fine(*, _json: bool = False) -> None:
        calls.append(_json)

    assert app.run(["fine", "--json-x"]) == 0
    assert calls == [True]
    assert capsys.readouterr().err == ""


def test_reserved_option_rejection_leaves_the_command_unregistered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    with pytest.raises(ValueError, match="'--json'"):

        @app.command
        def bad(*, _json: bool = False) -> None:
            pass

    assert app.run(["bad"]) == 2
    assert "Unknown command" in capsys.readouterr().err

    @app.command(name="bad")
    def bad_replacement() -> str:
        return "ok"

    assert app.run(["bad"]) == 0
    assert json.loads(capsys.readouterr().out) == "ok"


def test_default_command_reserving_an_option_is_rejected() -> None:
    def bad(*, _json: bool = False) -> None:
        pass

    with pytest.raises(ValueError, match="'--json'"):
        _ = fromargs.App("t", default_command=bad)


def test_default_registered_twice_raises_value_error() -> None:
    app = fromargs.App("t", default_command=lambda: None)

    with pytest.raises(ValueError):

        @app.default
        def second() -> str:
            return "ran"


def test_group_default_command_reserving_an_option_is_rejected() -> None:
    app = fromargs.App("t")

    def bad(*, _json: bool = False) -> None:
        pass

    with pytest.raises(ValueError, match="'--json'"):
        _ = app.group("sub", default_command=bad)


def test_group_without_version_inherits_the_root_version(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t", version="1.2.3")
    _ = app.group("sub")

    assert app.run(["sub", "--version"]) == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


def test_default_limit_on_a_group_truncates_its_result(capsys: pytest.CaptureFixture[str]) -> None:
    app = fromargs.App("t")
    sub = app.group("sub")

    @sub.default(limit=1)
    def main() -> list[int]:
        return [1, 2]

    assert app.run(["sub"]) == 0
    assert "note: showing 1 of 2" in capsys.readouterr().err


def test_root_default_validator_error_reports_the_adr001_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = fromargs.App("t")

    def rejecting(**_kwargs: object) -> None:
        raise ValueError("nope")

    @app.default(validator=rejecting)
    def main() -> str:
        return "ran"

    assert app.run([]) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "nope", "exit_code": 2}


def test_json_string_parameters_still_parse() -> None:
    received: list[object] = []
    app = fromargs.App("t")

    @app.command
    def conf(*, point: Point, numbers: list[int]) -> None:
        received.extend([point, numbers])

    assert app.run(["conf", "--point", '{"a": 1}', "--numbers", "[1, 2]"]) == 0
    assert received == [Point(a=1), [1, 2]]


def _app_built_in(module_globals: dict[str, object], **kwargs: object) -> fromargs.App:
    """Build ``fromargs.App("t", **kwargs)`` as if a module with ``module_globals`` called it."""
    namespace: dict[str, object] = {**module_globals, "fromargs": fromargs, "kwargs": kwargs}
    exec("app = fromargs.App('t', **kwargs)", namespace)
    app = namespace["app"]
    assert isinstance(app, fromargs.App)
    return app


def test_version_comes_from_the_calling_distribution(capsys: pytest.CaptureFixture[str]) -> None:
    from importlib.metadata import version

    app = _app_built_in({"__name__": "pytest.consumer"})

    assert app.run(["--version"]) == 0
    assert capsys.readouterr().out.strip() == version("pytest")


def test_version_maps_an_import_name_to_its_distribution(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from importlib.metadata import version

    app = _app_built_in({"__name__": "attr.consumer"})  # import name attr, distribution attrs

    assert app.run(["--version"]) == 0
    assert capsys.readouterr().out.strip() == version("attrs")


def test_version_uses_package_root_from_module_spec(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from importlib.metadata import version
    from types import SimpleNamespace

    app = _app_built_in(
        {"__name__": "__main__", "__spec__": SimpleNamespace(name="attr.__main__")}
    )

    assert app.run(["--version"]) == 0
    assert capsys.readouterr().out.strip() == version("attrs")


def test_version_ignores_module_spec_for_non_main_callers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from importlib.metadata import version
    from types import SimpleNamespace

    app = _app_built_in(
        {"__name__": "attr.consumer", "__spec__": SimpleNamespace(name="pytest.__main__")}
    )

    assert app.run(["--version"]) == 0
    assert capsys.readouterr().out.strip() == version("attrs")


def test_version_falls_back_to_the_calling_module_dunder_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = _app_built_in({"__name__": "fromargs_consumer_probe", "__version__": "9.9.9"})

    assert app.run(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "9.9.9"


def test_explicit_version_is_kept(capsys: pytest.CaptureFixture[str]) -> None:
    app = _app_built_in({"__name__": "pytest.consumer"}, version="1.2.3")

    assert app.run(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "1.2.3"


def test_version_falls_back_to_0_0_0_with_no_distribution_and_no_dunder_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = _app_built_in({"__name__": "fromargs_consumer_probe_unversioned"})

    assert app.run(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.0.0"
