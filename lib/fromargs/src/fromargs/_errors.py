"""The error type that ``run`` reports as one stderr line."""

from __future__ import annotations


class CliError(Exception):
    """One-line error; ``run`` reports it on stderr and returns ``exit_code``."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code: int = exit_code


def contract_error(exc: Exception, *, context: str) -> CliError:
    """Wrap a contract-violation exception as a ``CliError`` that exits 3."""
    return CliError(f"{context}: {exc}", exit_code=3)
