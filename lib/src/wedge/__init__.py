"""wedge: shiv a fromargs CLI into a skill as a content-addressed .pyz.

A skill ships a small committed launcher (``<skill>/scripts/<name>``) plus a
committed lock (``<skill>/scripts/<name>.wedge.json``). The ``.pyz`` itself
is never committed: a post-merge job builds it reproducibly and uploads it
as a content-addressed GitHub release asset. The launcher downloads it once,
verifies its sha256 against the lock, caches it, and execs it.

See ``wedge build``, ``wedge lock``, ``wedge check``, and ``wedge publish``.
"""

from wedge._cli import main

__all__ = ["main"]
