"""Argv repairs and scans that run around the single Cyclopts parse.

Quote repair uses only a verified split. The global ``--json``/``--full``
flags are stripped from anywhere before the end-of-options marker; they are
never hoisted and never passed to a handler. Only a verified quote split
prints a plain-text ``note:`` line on stderr. Probing only parses; handlers
never run here.
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable, Sequence
from inspect import BoundArguments
from typing import cast, get_args, get_origin

from cyclopts import App, CycloptsError
from cyclopts.annotations import is_union

from fromargs._errors import CliError

GLOBAL_FLAGS = frozenset({"--json", "--full"})


def strip_global_flags(app: App, argv: Sequence[str]) -> tuple[list[str], bool]:
    """Remove bare ``--json``/``--full`` tokens before the end-of-options marker.

    ``--json`` is a no-op; ``--full`` turns off truncation. Neither flag ever
    reaches a handler or Cyclopts. Returns the stripped tokens and whether
    ``--full`` was present.
    """
    tokens = list(argv)
    boundary = len(_options(app, tokens))
    before, after = tokens[:boundary], tokens[boundary:]
    kept = [token for token in before if token not in GLOBAL_FLAGS]
    return kept + after, "--full" in before


def repair_rejected(app: App, argv: Sequence[str]) -> list[str] | None:
    """Return the one verified split of an argv the app rejected, or ``None``."""
    original = list(argv)
    control_flags = _control_flags(app, original)
    if control_flags.intersection(original):
        return None
    splittable = _splittable_options(app, original)
    if splittable is None:
        return None
    found: tuple[list[str], str, list[str]] | None = None
    for index, token in enumerate(_options(app, original)):
        pieces = _pieces(token)
        if pieces is None or control_flags.intersection(pieces):
            continue
        previous = original[index - 1] if index else ""
        option = token.partition("=")[0] if token.startswith("--") else previous
        if option not in splittable:
            continue
        candidate = original[:index] + pieces + original[index + 1 :]
        probe, _ = strip_global_flags(app, candidate)
        if not _parses(app, probe):
            continue
        if found is not None:
            return None
        found = (candidate, token, pieces)
    if found is None:
        return None
    candidate, token, pieces = found
    print(f"note: split quoted argument {token!r} into {pieces!r}", file=sys.stderr)
    return candidate


def command_chain(app: App, argv: Sequence[str]) -> tuple[App, ...] | None:
    """The resolved chain of command apps for ``argv``, or ``None`` when parsing fails."""
    try:
        _, apps, _ = app.parse_commands(list(argv))
    except (CycloptsError, ValueError):
        return None
    return apps


def parse_once(app: App, argv: Sequence[str]) -> tuple[Callable[..., object], BoundArguments]:
    """Parse ``argv`` once; a converter's ``ValueError`` is reported as a ``CycloptsError``."""
    try:
        handler, bound, _ = app.parse_args(
            list(argv), print_error=False, exit_on_error=False, help_on_error=False
        )
    except ValueError as exc:
        raise CycloptsError(msg=str(exc)) from exc
    return handler, bound


def _parses(app: App, argv: Sequence[str]) -> bool:
    """Probe-parse ``argv``; a converter's ``CliError`` counts as a rejection."""
    try:
        _ = parse_once(app, argv)
    except (CycloptsError, CliError):
        return False
    return True


def _control_flags(app: App, argv: Sequence[str]) -> frozenset[str]:
    """Help and version flags from every app in the parsed command chain."""
    apps = command_chain(app, argv) or (app,)
    flags: set[str] = set()
    for command_app in apps:
        flags.update(command_app.help_flags)
        flags.update(command_app.version_flags)
    return frozenset(flags)


def _options(app: App, argv: Sequence[str]) -> list[str]:
    """The tokens before the command's end-of-options marker (``--`` by default).

    The innermost command app with a configured marker decides, as in Cyclopts.
    """
    tokens = list(argv)
    apps = command_chain(app, tokens) or (app,)
    configured = [
        command_app.end_of_options_delimiter
        for command_app in apps
        if command_app.end_of_options_delimiter is not None
    ]
    delimiter = configured[-1] if configured else "--"
    if delimiter and delimiter in tokens:
        return tokens[: tokens.index(delimiter)]
    return tokens


def _splittable_options(app: App, argv: Sequence[str]) -> set[str] | None:
    """Names of the options that can take a split value: no flags, no free-text ``str``."""
    apps = command_chain(app, argv)
    if apps is None:
        return None
    options: set[str] = set()
    for command_app in apps:
        try:
            arguments = command_app.assemble_argument_collection()
        except ValueError:
            continue
        for argument in arguments:
            if argument.is_flag():
                continue
            if _is_free_text(cast(object, argument.hint)) and argument.get_choices() is None:
                continue
            options.update(argument.names)
    return options


def _is_free_text(hint: object) -> bool:
    """True when ``hint`` is unstructured text: ``str``, a path-like, or a sequence of them."""
    if isinstance(hint, type):
        cls = cast("type[object]", hint)
        if issubclass(cls, str) or hasattr(cls, "__fspath__"):
            return True
    if is_union(hint):  # pyright: ignore[reportArgumentType]  # cyclopts types this as type | None but accepts UnionType/Annotated hints at runtime
        return all(
            argument is type(None) or _is_free_text(argument)
            for argument in cast("tuple[object, ...]", get_args(hint))
        )
    origin = get_origin(hint)
    if origin in (list, tuple, set, frozenset, Sequence):
        args = tuple(
            argument
            for argument in cast("tuple[object, ...]", get_args(hint))
            if argument is not Ellipsis
        )
        return bool(args) and all(_is_free_text(argument) for argument in args)
    return False


def _pieces(token: str) -> list[str] | None:
    """Shell-split ``token`` when it looks like several merged arguments."""
    if not any(character.isspace() for character in token):
        return None
    try:
        pieces = shlex.split(token)
    except ValueError:
        return None
    if len(pieces) < 2 or not any(
        piece.startswith("-") and len(piece) > 1 for piece in pieces
    ):
        return None
    return pieces