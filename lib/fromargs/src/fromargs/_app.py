"""``App``: a small wrapper that composes a Cyclopts app and forces JSON output.

Register a command with ``@app.command`` and a nested command group with
``app.group(name)``. A handler returns data, not text: ``run`` serializes
the return value as one JSON document. A handler must not declare a CLI
option named ``--json`` or ``--full``; those names are reserved for the
global flags that ``run`` owns.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Coroutine, Iterable, Sequence
from importlib import metadata
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
        if "version" not in cyclopts_kwargs:
            frame = inspect.currentframe()
            caller = frame.f_back if frame is not None else None
            cyclopts_kwargs["version"] = _caller_version(caller.f_globals if caller is not None else {})
        self._cyclopts: cyclopts.App = cyclopts.App(name=name, help=help, **cyclopts_kwargs)
        self._limits: dict[int, int] = {}
        if cyclopts_kwargs.get("default_command") is not None:
            reserved = _reserved_option(self._cyclopts)
            if reserved is not None:
                raise ValueError(f"command option {reserved!r} is reserved by fromargs")

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
        before = set(self._cyclopts)
        _ = self._cyclopts.command(obj, name=name, **kwargs)
        registered = sorted(set(self._cyclopts) - before)
        sub_app = self._cyclopts[registered[0]]
        reserved = _reserved_option(sub_app)
        if reserved is not None:
            for key in registered:
                del self._cyclopts[key]
            raise ValueError(f"command option {reserved!r} is reserved by fromargs")
        if limit is not None:
            self._limits[id(sub_app)] = limit
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


def _reserved_option(app: cyclopts.App) -> str | None:
    """The first reserved global flag name ``app``'s assembled arguments claim, or ``None``."""
    try:
        arguments = app.assemble_argument_collection()
    except ValueError:
        return None
    names: set[str] = set()
    for argument in arguments:
        names.update(argument.names)
    reserved = sorted(GLOBAL_FLAGS.intersection(names))
    return reserved[0] if reserved else None


def _caller_version(module_globals: dict[str, object]) -> Callable[[], str]:
    """Resolve ``--version`` for the module that built the ``App``, not for ``fromargs``.

    Cyclopts reads the version of the module that constructs ``cyclopts.App``;
    here that is always ``fromargs._app``. This mirrors Cyclopts' lookup for
    the caller instead: its distribution version, then its ``__version__``,
    then ``0.0.0``.
    """

    def resolve() -> str:
        root = str(module_globals.get("__name__", "")).split(".")[0]
        try:
            return metadata.version(root)
        except (metadata.PackageNotFoundError, ValueError):
            return str(module_globals.get("__version__", "0.0.0"))

    return resolve