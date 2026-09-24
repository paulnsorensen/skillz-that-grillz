"""``App``: a small wrapper that composes a Cyclopts app and forces JSON output.

Register a command with ``@app.command`` and a nested command group with
``app.group(name)``. A handler returns data, not text: ``run`` serializes
the return value as one JSON document. A handler must not declare a
parameter named ``json`` or ``full``; those names are reserved for the
global flags that ``run`` owns.
"""

from __future__ import annotations

import functools
import inspect
import sys
from collections.abc import Callable, Sequence
from typing import TextIO, TypeVar

import cyclopts

from fromargs import _run

T = TypeVar("T", bound=Callable[..., object])

_RESERVED_PARAMETERS = frozenset({"json", "full"})


class App:
    """Composes a ``cyclopts.App``; commands register through decorators and return data."""

    def __init__(
        self, name: str | None = None, *, help: str | None = None, **cyclopts_kwargs: object
    ) -> None:
        self._cyclopts = cyclopts.App(name=name, help=help, **cyclopts_kwargs)

    @classmethod
    def _wrap(cls, cyclopts_app: cyclopts.App) -> App:
        """Wrap an existing ``cyclopts.App`` without building a new one."""
        wrapper = cls.__new__(cls)
        wrapper._cyclopts = cyclopts_app
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
        unless ``--full`` is passed. ``obj`` must not declare a parameter
        named ``json`` or ``full``.
        """
        if obj is None:
            return functools.partial(self.command, name=name, limit=limit, **kwargs)
        _reject_reserved_parameters(obj)
        self._cyclopts.command(obj, name=name, **kwargs)
        if limit is not None:
            obj.__fromargs_limit__ = limit
        return obj

    def group(self, name: str, *, help: str | None = None) -> App:
        """Return a nested command group registered under this app."""
        sub = cyclopts.App(name=name, help=help)
        self._cyclopts.command(sub)
        return App._wrap(sub)

    def run(self, argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
        """Parse argv once, invoke one handler, and return its exit status."""
        return _run.run(self._cyclopts, argv=argv, stdout=stdout)

    def main(self) -> None:
        """Run with ``sys.argv`` and exit the process with the returned status."""
        sys.exit(self.run())


def _reject_reserved_parameters(handler: Callable[..., object]) -> None:
    """Raise ``ValueError`` when ``handler`` declares a ``json`` or ``full`` parameter."""
    reserved = sorted(_RESERVED_PARAMETERS.intersection(inspect.signature(handler).parameters))
    if reserved:
        raise ValueError(
            f"command parameter {reserved[0]!r} is reserved by fromargs"
        )
