"""The error type that ``run`` reports as one stderr line."""

from __future__ import annotations


class CliError(Exception):
    """One-line error; ``run`` reports it on stderr and returns ``exit_code``.

    ``exit_code`` must be an ``int`` from 2 to 255. Exit 0 means success, exit 1
    means an unexpected exception, and POSIX truncates statuses above 255.
    Otherwise ``TypeError`` (not an ``int``, or a ``bool``) or ``ValueError``.
    """

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"exit_code must be an int, got {type(exit_code).__name__}")
        if not 2 <= exit_code <= 255:
            raise ValueError(f"exit_code must be 2..255, got {exit_code}")
        super().__init__(message)
        self.exit_code: int = exit_code


def contract_error(exc: Exception, *, context: str) -> CliError:
    """Wrap a contract-violation exception as a ``CliError`` that exits 3."""
    return CliError(f"{context}: {exc}", exit_code=3)
