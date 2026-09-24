"""Argv repairs and scans that run around the single Cyclopts parse.

Quote repair uses only a verified split. Leading flags move before a resolved
leaf command, or before an unresolved command to preserve parser suggestions.
Only verified moves print a plain-text ``note:`` line on stderr.
Probing only parses; handlers never run here.
"""

from __future__ import annotations

import itertools
import os
import shlex
import sys
import types
from collections.abc import Sequence
from typing import Union, get_args, get_origin

from cyclopts import App, CoercionError, CycloptsError, convert

from fromargs._errors import CliError

JSON_FLAG = "--json"
_HOISTABLE_FLAGS = frozenset({JSON_FLAG, "--full"})


def hoist_leading_flags(app: App, argv: Sequence[str]) -> list[str]:
    """Move leading ``--json``/``--full`` flags after a registered command.

    Only valueless global flags move. The value of any other flag can equal a
    command name, so a guess can run the wrong command.
    """
    tokens = list(argv)
    leading = list(itertools.takewhile(lambda token: token.startswith("-"), tokens))
    if not leading or not _HOISTABLE_FLAGS.issuperset(leading):
        return tokens
    rest = tokens[len(leading) :]
    try:
        command, apps, _ = app.parse_commands(rest)
    except (CycloptsError, ValueError):
        command, apps = (), ()
    if (
        command
        and apps[-1].default_command is not None
        and rest[: len(command)] == list(command)
    ):
        print(
            f"note: moved {' '.join(leading)} after {' '.join(command)!r}",
            file=sys.stderr,
        )
        return [*command, *leading, *rest[len(command) :]]
    prefix = list(itertools.takewhile(lambda token: not token.startswith("-"), rest))
    return [*prefix, *leading, *rest[len(prefix) :]] if prefix else tokens


def json_requested(app: App, argv: Sequence[str]) -> bool:
    """True when an option token asks for JSON: ``--json`` or ``--json=<true>``."""
    for token in _options(app, argv):
        flag, equals, value = token.partition("=")
        if flag == JSON_FLAG and (not equals or _is_true(value)):
            return True
    return False


def repair_argv(app: App, argv: Sequence[str]) -> list[str]:
    """Return one verified split of shell-merged arguments, or argv unchanged.

    A shell caller can quote several arguments as one token. The split is used
    only when Cyclopts rejects the original argv and accepts exactly one
    candidate. Tokens after the end-of-options marker are never split.
    """
    original = list(argv)
    if _parses(app, original):
        return original
    repaired = repair_rejected(app, original)
    return original if repaired is None else repaired


def repair_rejected(app: App, argv: Sequence[str]) -> list[str] | None:
    """Return the one verified split of an argv the app rejected, or ``None``."""
    original = list(argv)
    control_flags = _control_flags(app, original)
    if control_flags.intersection(original):
        return None
    splittable: set[str] | None = None
    found: tuple[list[str], str, list[str]] | None = None
    for index, token in enumerate(_options(app, original)):
        pieces = _pieces(token)
        if pieces is None or control_flags.intersection(pieces):
            continue
        if splittable is None:
            splittable = _splittable_options(app, original)
        previous = original[index - 1] if index else ""
        option = token.partition("=")[0] if token.startswith("--") else previous
        if option not in splittable:
            continue
        candidate = original[:index] + pieces + original[index + 1 :]
        if not _parses(app, candidate):
            continue
        if found is not None:
            return None
        found = (candidate, token, pieces)
    if found is None:
        return None
    candidate, token, pieces = found
    print(f"note: split quoted argument {token!r} into {pieces!r}", file=sys.stderr)
    return candidate


def _control_flags(app: App, argv: Sequence[str]) -> frozenset[str]:
    """Help and version flags from every app in the parsed command chain."""
    try:
        _, apps, _ = app.parse_commands(list(argv))
    except (CycloptsError, ValueError):
        apps = (app,)
    flags: set[str] = set()
    for command_app in apps:
        flags.update(command_app.help_flags)
        flags.update(command_app.version_flags)
    return frozenset(flags)


def _is_true(value: str) -> bool:
    """Read ``value`` with Cyclopts' own boolean words; an invalid word is false."""
    try:
        return convert(bool, [value]) is True
    except CoercionError:
        return False


def _options(app: App, argv: Sequence[str]) -> list[str]:
    """The tokens before the command's end-of-options marker (``--`` by default).

    The innermost command app with a configured marker decides, as in Cyclopts.
    """
    tokens = list(argv)
    try:
        _, apps, _ = app.parse_commands(tokens)
    except (CycloptsError, ValueError):
        apps = (app,)
    configured = [
        command_app.end_of_options_delimiter
        for command_app in apps
        if command_app.end_of_options_delimiter is not None
    ]
    delimiter = configured[-1] if configured else "--"
    if delimiter and delimiter in tokens:
        return tokens[: tokens.index(delimiter)]
    return tokens


def _parses(app: App, argv: Sequence[str]) -> bool:
    """Probe-parse ``argv``; a converter's ``CliError`` counts as a rejection."""
    try:
        app.parse_args(
            list(argv), print_error=False, exit_on_error=False, help_on_error=False
        )
    except (CycloptsError, CliError, ValueError):
        return False
    return True


def _splittable_options(app: App, argv: Sequence[str]) -> set[str]:
    """Names of the options that can take a split value: no flags, no free-text ``str``."""
    try:
        _, apps, _ = app.parse_commands(list(argv))
    except (CycloptsError, ValueError):
        return set()
    options: set[str] = set()
    for command_app in apps:
        try:
            arguments = command_app.assemble_argument_collection()
        except ValueError:
            continue
        for argument in arguments:
            if argument.is_flag():
                continue
            if _is_free_text(argument.hint) and argument.get_choices() is None:
                continue
            options.update(argument.names)
    return options


def _is_free_text(hint: object) -> bool:
    """True when ``hint`` is unstructured text: ``str``, ``Path``, or a sequence of them."""
    if hint is str or (isinstance(hint, type) and issubclass(hint, (str, os.PathLike))):
        return True
    origin = get_origin(hint)
    if origin is Union or origin is types.UnionType:
        return all(
            argument is type(None) or _is_free_text(argument) for argument in get_args(hint)
        )
    if origin in (list, tuple, set, frozenset, Sequence):
        args = tuple(argument for argument in get_args(hint) if argument is not Ellipsis)
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
