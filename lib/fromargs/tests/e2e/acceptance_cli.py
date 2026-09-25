"""Fixture CLI for the fromargs end-to-end acceptance suite.

``test_acceptance.py`` runs this file as a subprocess and never imports it.
Every command appends one JSON line per invocation to the file named by the
``FROMARGS_E2E_CALL_LOG`` environment variable, so a test can check "the
handler ran exactly once" or "no handler ran" from outside the process.

Keep every coupling to the ``fromargs`` public API in this one file. The
acceptance tests in ``test_acceptance.py`` only know about argv, exit codes,
stdout, stderr, and the call log; they do not import command internals.

Set ``FROMARGS_E2E_REGISTER_BAD`` to ``json`` or ``full`` to skip the normal
command surface and instead attempt one reserved-parameter registration,
reporting the outcome as a JSON stdout line with exit 0 (raised) or 1
(did not raise) -- this is how AC-8 is observed from a subprocess.
"""

# No `from __future__ import annotations`: Cyclopts resolves command hints
# at parse time, as in ``examples/cheese_cave.py``.

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Annotated

from cyclopts import Parameter, Token

import fromargs


def _log(command: str, args: dict[str, object]) -> None:
    """Append one call record to the log file named by the env var, if set."""
    path = os.environ.get("FROMARGS_E2E_CALL_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"command": command, "args": args}) + "\n")


def _lenient(type_: object, tokens: Sequence[Token]) -> int:
    """Accept any single token as ``0``; used only to build an AC-11 fixture."""
    return 0


Lenient = Annotated[int, Parameter(converter=_lenient)]


@dataclass
class Point:
    """A tiny dataclass parameter for a native-Cyclopts-conversion check."""

    a: int


def _register_widget(app: fromargs.App) -> None:
    @app.command
    def widget(
        name: str = "ok",
        *words: str,
        count: int = 1,
        max_count: int = 1,
        tags: list[int] | None = None,
        label: str = "",
        dry_run: bool = False,
    ) -> int | None:
        """AC-1, AC-2, AC-3, AC-11, AC-14 fixture: dispatch, binding, repairs."""
        _log(
            "widget",
            {
                "name": name,
                "words": list(words),
                "count": count,
                "max_count": max_count,
                "tags": tags,
                "label": label,
                "dry_run": dry_run,
            },
        )
        if name.lstrip("-").isdigit():
            return int(name)
        return None


def _register_crate(app: fromargs.App) -> None:
    crate = app.group("crate", help="A nested command group.")

    @crate.command(name="show")
    def crate_show(id_: int) -> dict[str, int]:
        """AC-1 fixture: a decorator-registered command inside a group."""
        _log("crate show", {"id_": id_})
        return {"id": id_}


def _register_fail(app: fromargs.App) -> None:
    @app.command
    def fail(kind: str) -> None:
        """AC-4 fixture: a handler that always raises."""
        _log("fail", {"kind": kind})
        if kind == "contract":
            raise fromargs.contract_error(ValueError("bad shape"), context="load")
        raise fromargs.CliError("boom")


def _register_ambiguous(app: fromargs.App) -> None:
    @app.command
    def ambiguous(*, first: Lenient = 0, second: Lenient = 0, count: int) -> None:
        """AC-11 fixture: two independently-splittable options, both required.

        The literal argv never supplies ``count`` directly; only a verified
        split of ``first`` or ``second`` can supply it. Both splits succeed,
        so the repair must be refused as ambiguous.
        """
        _log("ambiguous", {"first": first, "second": second, "count": count})


def _register_ranked(app: fromargs.App) -> None:
    @app.command(limit=3)
    def ranked(*, top: int = 5) -> list[int]:
        """AC-6, AC-7 fixture: a truncated sequence result with a splittable option."""
        return list(range(top))


def _register_conf(app: fromargs.App) -> None:
    @app.command
    def conf(*, point: Point, numbers: list[int]) -> dict[str, object]:
        """Native-Cyclopts fixture: a dataclass and a ``list[int]`` from JSON text."""
        result = {"point": asdict(point), "numbers": numbers}
        _log("conf", result)
        return result


def _register_guarded(app: fromargs.App) -> None:
    @app.command
    def guarded(text: str = "", *, label: str = "", count: int) -> None:
        """AC-11 fixture: a required option only a bad split could supply.

        ``count`` has no default, so an argv that omits it is always
        rejected and the repair probe always runs. ``label`` is free-text
        ``str`` and ``text`` sits after the end-of-options marker in the
        double-dash test; the probe must never split either to invent the
        missing ``count``.
        """
        _log("guarded", {"text": text, "label": label, "count": count})


def build_app() -> fromargs.App:
    app = fromargs.App(name="acceptance-cli", help="fromargs end-to-end acceptance fixture.")
    _register_widget(app)
    _register_crate(app)
    _register_fail(app)
    _register_ambiguous(app)
    _register_ranked(app)
    _register_conf(app)
    _register_guarded(app)
    return app


def _register_bad(name: str) -> int:
    """AC-8: attempt to register a command with a reserved parameter.

    Prints one JSON line to stdout and returns 0 when registration raised
    ``ValueError`` as required, 1 otherwise.
    """
    app = fromargs.App(name="reserved-check")
    try:
        if name == "json":

            @app.command
            def bad(*, json: bool = False) -> bool:
                return json
        else:

            @app.command
            def bad(*, full: bool = False) -> bool:
                return full
    except ValueError as exc:
        print(json.dumps({"raised": True, "error": str(exc)}))
        return 0
    print(json.dumps({"raised": False}))
    return 1


def main() -> int:
    register_bad = os.environ.get("FROMARGS_E2E_REGISTER_BAD")
    if register_bad:
        return _register_bad(register_bad)
    return build_app().run()


if __name__ == "__main__":
    sys.exit(main())