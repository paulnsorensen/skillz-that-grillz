"""``App``: a small wrapper that composes a Cyclopts app and forces JSON output.

Register a command with ``@app.command`` and a nested command group with
``app.group(name)``. A handler returns data, not text: ``run`` serializes
the return value as one JSON document. A handler must not declare a CLI
option named ``--json`` or ``--full``; those names are reserved for the
global flags that ``run`` owns.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Coroutine, Iterable, Sequence
from typing import TYPE_CHECKING, Literal, TextIO, TypedDict, TypeVar, Unpack, overload

import cyclopts

from fromargs._argv import GLOBAL_FLAGS
from fromargs._run import run as _run

if TYPE_CHECKING:
    from cyclopts.help.protocols import HelpFormatter
    from rich.console import Console

T = TypeVar("T", bound=Callable[..., object])

_HelpFormat = Literal["markdown", "md", "plaintext", "restructuredtext", "rst", "rich"]


class _AppKwargs(TypedDict, total=False):
    """Keyword-only ``cyclopts.App`` constructor arguments this wrapper forwards untouched."""

    usage: str | None
    alias: str | Iterable[str] | None
    synonym: str | Iterable[str] | None
    default_command: Callable[..., object] | None
    default_parameter: cyclopts.Parameter | None
    config: (
        Callable[[cyclopts.App, tuple[str, ...], cyclopts.ArgumentCollection], object]
        | Iterable[Callable[[cyclopts.App, tuple[str, ...], cyclopts.ArgumentCollection], object]]
        | None
    )
    version: str | Callable[..., str] | Callable[..., Coroutine[object, object, str]] | None
    version_flags: str | Iterable[str] | None
    show: bool
    console: Console | None
    error_console: Console | None
    help_flags: str | Iterable[str] | None
    help_format: _HelpFormat | None
    help_on_error: bool | None
    help_prologue: str | None
    help_epilogue: str | None
    version_format: _HelpFormat | None
    group: cyclopts.Group | str | Iterable[cyclopts.Group | str] | None
    group_arguments: str | cyclopts.Group | None
    group_parameters: str | cyclopts.Group | None
    group_commands: str | cyclopts.Group | None
    validator: Callable[..., object] | Iterable[Callable[..., object]] | None
    name_transform: Callable[[str], str] | None
    sort_key: object
    end_of_options_delimiter: str | None
    print_error: bool | None
    exit_on_error: bool | None
    verbose: bool | None
    suppress_keyboard_interrupt: bool
    backend: Literal["asyncio", "trio"] | None
    help_formatter: Literal["default", "plain"] | HelpFormatter | None
    error_formatter: Callable[[cyclopts.CycloptsError], object] | None
    result_action: cyclopts.ResultAction | None


class App:
    """Composes a ``cyclopts.App``; commands register through decorators and return data."""

    def __init__(
        self,
        name: str | None = None,
        *,
        help: str | None = None,
        **cyclopts_kwargs: Unpack[_AppKwargs],
    ) -> None:
        self._cyclopts: cyclopts.App = cyclopts.App(name=name, help=help, **cyclopts_kwargs)
        self._limits: dict[int, int] = {}

    @classmethod
    def _wrap(cls, cyclopts_app: cyclopts.App, limits: dict[int, int]) -> App:
        """Wrap an existing ``cyclopts.App`` without building a new one."""
        wrapper = cls.__new__(cls)
        wrapper._cyclopts = cyclopts_app
        wrapper._limits = limits
        return wrapper

    @overload
    def command(
        self,
        obj: T,
        *,
        name: str | Sequence[str] | None = None,
        limit: int | None = None,
        **kwargs: Unpack[_AppKwargs],
    ) -> T: ...

    @overload
    def command(
        self,
        obj: None = None,
        *,
        name: str | Sequence[str] | None = None,
        limit: int | None = None,
        **kwargs: Unpack[_AppKwargs],
    ) -> Callable[[T], T]: ...

    def command(
        self,
        obj: T | None = None,
        *,
        name: str | Sequence[str] | None = None,
        limit: int | None = None,
        **kwargs: Unpack[_AppKwargs],
    ) -> T | Callable[[T], T]:
        """Register ``obj`` as a command.

        ``limit`` truncates a sequence result to its first ``limit`` items
        unless ``--full`` is passed; it must not be negative. ``obj`` must
        not declare a CLI option named ``--json`` or ``--full``.
        """
        if obj is None:

            def register(handler: T) -> T:
                return self.command(handler, name=name, limit=limit, **kwargs)

            return register
        if limit is not None and limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        _reject_reserved_options(obj)
        _ = self._cyclopts.command(obj, name=name, **kwargs)
        if limit is not None:
            self._limits[id(self._sub_app(obj, name))] = limit
        return obj

    def group(self, name: str, *, help: str | None = None) -> App:
        """Return a nested command group registered under this app."""
        sub = cyclopts.App(name=name, help=help)
        _ = self._cyclopts.command(sub)
        return App._wrap(sub, self._limits)

    def run(self, argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
        """Parse argv once, invoke one handler, and return its exit status."""
        return _run(self._cyclopts, argv=argv, stdout=stdout, limits=self._limits)

    def main(self) -> None:
        """Run with ``sys.argv`` and exit the process with the returned status."""
        sys.exit(self.run())

    def _sub_app(self, obj: Callable[..., object], name: str | Sequence[str] | None) -> cyclopts.App:
        """The ``cyclopts.App`` that ``self._cyclopts.command`` just registered ``obj`` under."""
        if isinstance(name, str):
            key = name
        elif name:
            key = name[0]
        else:
            key = self._cyclopts.name_transform(obj.__name__)
        return self._cyclopts[key]


def _reject_reserved_options(handler: Callable[..., object]) -> None:
    """Raise ``ValueError`` when ``handler``'s assembled CLI options reserve a global flag."""
    scratch = cyclopts.App()
    _ = scratch.default(handler)
    try:
        arguments = scratch.assemble_argument_collection()
    except ValueError:
        return
    names: set[str] = set()
    for argument in arguments:
        names.update(argument.names)
    reserved = sorted(GLOBAL_FLAGS.intersection(names))
    if reserved:
        raise ValueError(f"command option {reserved[0]!r} is reserved by fromargs")