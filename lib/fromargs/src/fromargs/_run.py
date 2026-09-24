"""Parse argv once, invoke one handler, and map the outcome to an exit status."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Callable, Coroutine, Sequence
from contextlib import nullcontext, redirect_stdout
from inspect import BoundArguments
from typing import TextIO

from cyclopts import App, CycloptsError

from fromargs._argv import hoist_leading_flags, json_requested, repair_rejected
from fromargs._errors import CliError


def run(
    app: App, *, argv: Sequence[str] | None = None, stdout: TextIO | None = None
) -> int:
    """Parse argv, invoke the command once, and return its exit status.

    Leading ``--json``/``--full`` flags move after the command first. A
    rejected argv gets one verified quote-split repair before it fails.

    The handler may return ``None`` (exit 0), an ``int`` that is not a
    ``bool``, or a coroutine that resolves to one of them. A coroutine runs
    through ``asyncio.run``. ``TypeError`` is raised for a ``str`` argv, for
    any other status, and for an async handler under a running event loop or
    a non-asyncio backend on the resolved command chain.

    Errors go to stderr as one line: ``ERROR: <message>``, or the JSON object
    ``{"error": <message>, "exit_code": <n>}`` when JSON output is requested.
    ``--json`` or ``--json=<true>`` before the end-of-options marker requests
    it. After a successful parse, the handler's bound ``json`` argument
    decides instead, and a non-bool ``json`` value means text.
    """
    if isinstance(argv, str):
        raise TypeError("argv must be a sequence of strings, not str")
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens and app.default_command is None:
        return _report("command required", 2, json_mode=False)
    tokens = hoist_leading_flags(app, tokens)
    json_mode = json_requested(app, tokens)
    context = redirect_stdout(stdout) if stdout is not None else nullcontext()
    with context:
        try:
            handler, bound = _parse(app, tokens)
        except CliError as exc:
            return _report(str(exc), exc.exit_code, json_mode=json_mode)
        except CycloptsError as exc:
            return _report(str(exc), 2, json_mode=json_mode)
        if "json" in bound.arguments:
            json_mode = bound.arguments["json"] is True
        try:
            status = handler(*bound.args, **bound.kwargs)
            if inspect.iscoroutine(status):
                status = _await(app, tokens, status)
        except CliError as exc:
            return _report(str(exc), exc.exit_code, json_mode=json_mode)
    if status is None:
        return 0
    if isinstance(status, bool) or not isinstance(status, int):
        raise TypeError(f"command returned non-integer status: {status!r}")
    return status


def _parse(app: App, tokens: list[str]) -> tuple[Callable[..., object], BoundArguments]:
    """Parse ``tokens``; on rejection, parse only a verified repair."""
    try:
        return _parse_once(app, tokens)
    except (CycloptsError, CliError):
        repaired = repair_rejected(app, tokens)
        if repaired is None:
            raise
        return _parse_once(app, repaired)


def _parse_once(
    app: App, tokens: list[str]
) -> tuple[Callable[..., object], BoundArguments]:
    try:
        handler, bound, _ = app.parse_args(
            tokens, print_error=False, exit_on_error=False, help_on_error=False
        )
    except ValueError as exc:
        raise CycloptsError(msg=str(exc)) from exc
    return handler, bound


def _await(
    app: App, tokens: list[str], coroutine: Coroutine[object, object, object]
) -> object:
    """Run an async handler only when its effective backend is asyncio."""
    _, apps, _ = app.parse_commands(tokens)
    backend = next(
        (command_app.backend for command_app in reversed(apps) if command_app.backend is not None),
        "asyncio",
    )
    if backend != "asyncio":
        coroutine.close()
        raise TypeError(f"async commands need the asyncio backend, not {backend!r}")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise TypeError("async commands cannot run inside a running event loop")


def _report(message: str, exit_code: int, *, json_mode: bool) -> int:
    if json_mode:
        line = json.dumps({"error": message, "exit_code": exit_code})
    else:
        line = f"ERROR: {' '.join(message.splitlines())}"
    print(line, file=sys.stderr)
    return exit_code
