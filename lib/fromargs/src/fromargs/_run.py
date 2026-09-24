"""Parse argv once, invoke one handler, and print its result as one JSON document."""

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

from fromargs._argv import repair_rejected, strip_global_flags
from fromargs._errors import CliError
from fromargs._output import write_result


def run(
    app: App, *, argv: Sequence[str] | None = None, stdout: TextIO | None = None
) -> int:
    """Parse argv, invoke the command once, and return its exit status.

    A bare ``--json`` or ``--full`` token anywhere before the end-of-options
    marker is stripped before parsing. ``--json`` is a no-op; ``--full``
    turns off result truncation. A rejected argv gets one verified
    quote-split repair before it fails.

    ``None`` from the handler means exit 0 with no stdout. Any other return
    value is printed as one JSON document and the command exits 0. A
    coroutine handler runs through ``asyncio.run``. ``TypeError`` is raised
    for a ``str`` argv, and for an async handler under a running event loop
    or a non-asyncio backend on the resolved command chain.

    Every error is one JSON line on stderr: ``{"error": <message>,
    "exit_code": <n>}``. Repair ``note:`` lines stay plain text on stderr.
    """
    if isinstance(argv, str):
        raise TypeError("argv must be a sequence of strings, not str")
    tokens = list(sys.argv[1:] if argv is None else argv)
    tokens, full = strip_global_flags(app, tokens)
    if not tokens and app.default_command is None:
        return _report("command required", 2)
    context = redirect_stdout(stdout) if stdout is not None else nullcontext()
    with context:
        try:
            handler, bound, tokens, full = _parse(app, tokens, full)
        except CliError as exc:
            return _report(str(exc), exc.exit_code)
        except CycloptsError as exc:
            return _report(str(exc), 2)
        try:
            status = handler(*bound.args, **bound.kwargs)
            if inspect.iscoroutine(status):
                status = _await(app, tokens, status)
        except CliError as exc:
            return _report(str(exc), exc.exit_code)
    if status is None:
        return 0
    limit = getattr(handler, "__fromargs_limit__", None)
    write_result(status, limit=limit, full=full, stdout=stdout)
    return 0


def _parse(
    app: App, tokens: list[str], full: bool
) -> tuple[Callable[..., object], BoundArguments, list[str], bool]:
    """Parse ``tokens``; on rejection, parse only a verified repair."""
    try:
        handler, bound = _parse_once(app, tokens)
        return handler, bound, tokens, full
    except (CycloptsError, CliError):
        repaired = repair_rejected(app, tokens)
        if repaired is None:
            raise
        repaired, more_full = strip_global_flags(app, repaired)
        handler, bound = _parse_once(app, repaired)
        return handler, bound, repaired, full or more_full


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


def _report(message: str, exit_code: int) -> int:
    print(json.dumps({"error": message, "exit_code": exit_code}), file=sys.stderr)
    return exit_code