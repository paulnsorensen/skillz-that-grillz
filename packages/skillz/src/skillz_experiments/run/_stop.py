"""The coded stop that ends a run.
"""
from __future__ import annotations

from skillz_experiments._cases import CodedError


class Stop(CodedError):
    """A non-interactive stop. The outer agent reads `data`, answers the question, and calls `run` again."""

    def __init__(self, code: str, message: str, data: dict[str, object]) -> None:
        super().__init__(code, message)
        self.data: dict[str, object] = {"stop": code, "message": message} | data
