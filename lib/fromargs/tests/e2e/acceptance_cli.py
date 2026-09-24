"""Fixture CLI for the fromargs end-to-end acceptance suite.

``test_acceptance.py`` runs this file as a subprocess and never imports it.
Every command appends one JSON line per invocation to the file named by the
``FROMARGS_E2E_CALL_LOG`` environment variable, so a test can check "the
handler ran exactly once" or "no handler ran" from outside the process.

Keep every coupling to the ``fromargs`` public API in this one file. The
acceptance tests in ``test_acceptance.py`` only know about argv, exit codes,
stdout, stderr, and the call log; they do not import command internals.
"""

# No `from __future__ import annotations`: Cyclopts resolves command hints
# at parse time, as in ``examples/cheese_cave.py``.

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Annotated

from cyclopts import App, Parameter, Token

import fromargs


def _log(command: str, args: dict[str, object]) -> None:
    """Append one call record to the log file named by the env var, if set."""
    path = os.environ.get("FROMARGS_E2E_CALL_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"command": command, "args": args}) + "\n")


def _lenient(type_: object, tokens: Sequence[Token]) -> int:
    """Accept any single token as ``0``; used only to build an AC-7 fixture."""
    return 0


Lenient = Annotated[int, Parameter(converter=_lenient)]


@dataclass
class Point:
    """A tiny dataclass parameter for the AC-10 JSON-string parsing check."""

    a: int


def build_app() -> App:
    app = App(name="acceptance-cli", help="fromargs end-to-end acceptance fixture.")

    @app.command
    def widget(
        name: str = "ok",
        *words: str,
        count: int = 1,
        max_count: int = 1,
        tags: list[int] | None = None,
        label: str = "",
        full: bool = False,
        json: bool = False,
    ) -> int | None:
        """AC-1, AC-5, AC-6, AC-7 fixture: dispatch, binding, and repairs."""
        _log(
            "widget",
            {
                "name": name,
                "words": list(words),
                "count": count,
                "max_count": max_count,
                "tags": tags,
                "label": label,
                "full": full,
                "json": json,
            },
        )
        if name.lstrip("-").isdigit():
            return int(name)
        return None

    @app.command
    def fail(kind: str, *, json: bool = False) -> None:
        """AC-2, AC-3 fixture: a handler that always raises."""
        _log("fail", {"kind": kind})
        if kind == "contract":
            raise fromargs.contract_error(ValueError("bad shape"), context="load")
        raise fromargs.CliError("boom", exit_code=5)

    @app.command
    def ambiguous(*, first: Lenient = 0, second: Lenient = 0, count: int) -> None:
        """AC-7 fixture: two independently-splittable options, both required.

        The literal argv never supplies ``count`` directly; only a verified
        split of ``first`` or ``second`` can supply it. Both splits succeed,
        so the repair must be refused as ambiguous.
        """
        _log("ambiguous", {"first": first, "second": second, "count": count})

    @app.command
    def emit_list(*, limit: int = 3, full: bool = False) -> None:
        """AC-9 fixture: text-mode list truncation."""
        fromargs.emit(["a", "b", "c", "d", "e"], limit=limit, full=full)

    @app.command
    def emit_dict(*, limit: int = 2) -> None:
        """AC-8 fixture: a ``dict`` value dumps whole even with ``limit`` set."""
        fromargs.emit({"items": [1, 2, 3, 4, 5]}, limit=limit)

    @app.command
    def emit_json_list(*, limit: int = 2) -> None:
        """AC-8 fixture: ``json_mode=True`` dumps a list whole, ignoring ``limit``."""
        fromargs.emit([1, 2, 3, 4, 5], json_mode=True, limit=limit)

    @app.command
    def conf(*, point: Point, numbers: list[int], json: bool = False) -> None:
        """AC-10 fixture: a dataclass and a ``list[int]`` still parse from JSON text."""
        _log("conf", {"point": asdict(point), "numbers": numbers, "json": json})

    return app


def main() -> int:
    return fromargs.run(build_app())


if __name__ == "__main__":
    sys.exit(main())
