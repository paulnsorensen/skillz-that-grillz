"""Parse argv once, invoke one handler, and print its result as one JSON document."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import sys
import tempfile
import traceback
from collections.abc import Callable, Coroutine, Sequence
from contextlib import nullcontext, redirect_stdout
from inspect import BoundArguments
from typing import TextIO

from cyclopts import App, CycloptsError

from fromargs._argv import command_chain, parse_once, repair_rejected, strip_global_flags
from fromargs._errors import CliError
from fromargs._output import write_result


class _AsyncContractError(TypeError):
    """A programmer-contract violation for an async handler; always re-raised."""


def run(
    app: App,
    *,
    argv: Sequence[str] | None = None,
    stdout: TextIO | None = None,
    limits: dict[int, int] | None = None,
) -> int:
    """Parse argv, invoke the command once, and return its exit status.

    A bare ``--json`` or ``--full`` token anywhere before the end-of-options
    marker is stripped before parsing. ``--json`` is a no-op; ``--full``
    turns off result truncation. A rejected argv gets one verified
    quote-split repair before it fails. A command chain that resolves to a
    bare help print without an explicit help flag is reported as a missing
    command instead of printing help.

    ``None`` from the handler means exit 0 with no stdout. Any other return
    value is printed as one JSON document and the command exits 0. A
    coroutine handler runs through ``asyncio.run``. ``TypeError`` is raised
    for a ``str`` argv, and for an async handler under a running event loop
    or a non-asyncio backend on the resolved command chain.

    Every ADR-001 error (a ``CliError`` or a rejected parse) is one JSON
    line on stderr: ``{"error": <message>, "exit_code": <n>}``. An
    unexpected exception from the handler or from result serialization
    (including a ``CycloptsError`` raised by the handler) instead gets a
    three-key envelope with a ``traceback`` path to a file holding the full
    traceback, and returns 1. Repair ``note:`` lines stay plain text on
    stderr.
    """
    if isinstance(argv, str):
        raise TypeError("argv must be a sequence of strings, not str")
    tokens = list(sys.argv[1:] if argv is None else argv)
    tokens, full = strip_global_flags(app, tokens)
    context = redirect_stdout(stdout) if stdout is not None else nullcontext()
    with context:
        try:
            handler, bound, tokens, full, apps = _parse(app, tokens, full)
        except CliError as exc:
            return _report(str(exc), exc.exit_code)
        except CycloptsError as exc:
            return _report(_safe_message(exc), 2)
        if _is_bare_help(handler) and not _requested_help(apps, tokens):
            return _report("command required", 2)
        try:
            status = handler(*bound.args, **bound.kwargs)
            if inspect.iscoroutine(status):
                status = _await(apps, status)
        except CliError as exc:
            return _report(str(exc), exc.exit_code)
        except _AsyncContractError:
            raise
        except Exception as exc:
            return _report_unexpected(exc)
    if status is None:
        return 0
    limit = None if limits is None else limits.get(id(apps[-1]))
    try:
        write_result(status, limit=limit, full=full, stdout=stdout)
    except Exception as exc:
        return _report_unexpected(exc)
    return 0


def _parse(
    app: App, tokens: list[str], full: bool
) -> tuple[Callable[..., object], BoundArguments, list[str], bool, tuple[App, ...]]:
    """Parse ``tokens``; on rejection, parse only a verified repair."""
    try:
        handler, bound = parse_once(app, tokens)
    except (CycloptsError, CliError):
        repaired = repair_rejected(app, tokens)
        if repaired is None:
            raise
        tokens, more_full = strip_global_flags(app, repaired)
        handler, bound = parse_once(app, tokens)
        full = full or more_full
    apps = command_chain(app, tokens) or (app,)
    return handler, bound, tokens, full, apps


def _is_bare_help(handler: Callable[..., object]) -> bool:
    """True when ``handler`` is a command app's unparametrized ``help_print``."""
    return inspect.ismethod(handler) and handler.__func__ is App.help_print


def _requested_help(apps: tuple[App, ...], tokens: Sequence[str]) -> bool:
    """True when ``tokens`` literally contains a help flag from the command chain."""
    flags: set[str] = set()
    for command_app in apps:
        flags.update(command_app.help_flags)
    return bool(flags.intersection(tokens))


def _await(apps: tuple[App, ...], coroutine: Coroutine[object, object, object]) -> object:
    """Run an async handler only when its effective backend is asyncio."""
    backend = next(
        (command_app.backend for command_app in reversed(apps) if command_app.backend is not None),
        "asyncio",
    )
    if backend != "asyncio":
        coroutine.close()
        raise _AsyncContractError(f"async commands need the asyncio backend, not {backend!r}")
    try:
        _ = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise _AsyncContractError("async commands cannot run inside a running event loop")


def _report(message: str, exit_code: int) -> int:
    print(json.dumps({"error": message, "exit_code": exit_code}), file=sys.stderr)
    return exit_code


def _safe_message(exc: CycloptsError) -> str:
    """Render ``exc`` for the ADR-001 envelope, even when ``str(exc)`` itself raises.

    Cyclopts' ``ValidationError.__str__`` raises ``NotImplementedError`` when
    none of ``argument``, ``group``, or ``command_chain`` is set, which
    happens for a root default handler's validator.
    """
    try:
        return str(exc)
    except Exception:
        return getattr(exc, "exception_message", "") or type(exc).__name__


def _report_unexpected(exc: Exception) -> int:
    """Report an unexpected exception as a two- or three-key envelope; return 1.

    The envelope gets a ``traceback`` path when the traceback file writes
    successfully. An ``OSError`` while creating or writing that file omits
    the ``traceback`` key instead of escaping the ADR-001 envelope.
    """
    envelope: dict[str, object] = {"error": f"{type(exc).__name__}: {exc}", "exit_code": 1}
    try:
        descriptor, path = tempfile.mkstemp(prefix="fromargs-", suffix=".traceback")
    except OSError:
        pass
    else:
        try:
            with open(descriptor, "w") as handle:
                _ = handle.write(traceback.format_exc())
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(path)
        else:
            envelope["traceback"] = path
    print(json.dumps(envelope), file=sys.stderr)
    return 1