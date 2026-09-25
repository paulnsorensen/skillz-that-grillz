"""``App``: a small wrapper that composes a Cyclopts app and forces JSON output.

Register a command with ``@app.command`` and a nested command group with
``app.group(name)``. A handler returns data, not text: ``run`` serializes
the return value as one JSON document. A handler must not declare a CLI
option named ``--json`` or ``--full``; those names are reserved for the
global flags that ``run`` owns.
"""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable, Sequence
from typing import TextIO, TypeVar

import cyclopts

from fromargs._argv import _GLOBAL_FLAGS
from fromargs._run import run as _run

T = TypeVar("T", bound=Callable[..., object])


class App:
    """Composes a ``cyclopts.App``; commands register through decorators and return data."""

    def __init__(
        self, name: str | None = None, *, help: str | None = None, **cyclopts_kwargs: object
    ) -> None:
        self._cyclopts = cyclopts.App(name=name, help=help, **cyclopts_kwargs)
        self._limits: dict[int, int] = {}

    @classmethod
    def _wrap(cls, cyclopts_app: cyclopts.App, limits: dict[int, int]) -> App:
        """Wrap an existing ``cyclopts.App`` without building a new one."""
        wrapper = cls.__new__(cls)
        wrapper._cyclopts = cyclopts_app
        wrapper._limits = limits
        return wrapper

    def command(
        self,
        obj: T | None = None,
        *,
        name: str | Sequence[str] | None = None,
        limit: int | None = None,
        **kwargs: object,
    ) -> T | Callable[[T], T]:
        """Register ``obj`` as a command.

        ``limit`` truncates a sequence result to its first ``limit`` items
        unless ``--full`` is passed; it must not be negative. ``obj`` must
        not declare a CLI option named ``--json`` or ``--full``.
        """
        if obj is None:
            return functools.partial(self.command, name=name, limit=limit, **kwargs)
        if limit is not None and limit < 0:
            raise ValueError(f"limit must not be negative, got {limit}")
        _reject_reserved_options(obj)
        self._cyclopts.command(obj, name=name, **kwargs)
        if limit is not None:
            self._limits[id(self._sub_app(obj, name))] = limit
        return obj

    def group(self, name: str, *, help: str | None = None) -> App:
        """Return a nested command group registered under this app."""
        sub = cyclopts.App(name=name, help=help)
        self._cyclopts.command(sub)
        return App._wrap(sub, self._limits)

    def run(self, argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
        """Parse argv once, invoke one handler, and return its exit status."""
        return _run(self._cyclopts, argv=argv, stdout=stdout, limits=self._limits)

    def main(self) -> None:
        """Run with ``sys.argv`` and exit the process with the returned status."""
        sys.exit(self.run())

    def _sub_app(self, obj: T, name: str | Sequence[str] | None) -> cyclopts.App:
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
    scratch.default(handler)
    try:
        arguments = scratch.assemble_argument_collection()
    except ValueError:
        return
    names: set[str] = set()
    for argument in arguments:
        names.update(argument.names)
    reserved = sorted(_GLOBAL_FLAGS.intersection(names))
    if reserved:
        raise ValueError(f"command option {reserved[0]!r} is reserved by fromargs")