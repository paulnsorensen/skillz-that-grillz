"""The coded stop that ends a run.
"""
from __future__ import annotations

from skillz_experiments._cases import CodedError


class Stop(CodedError):
    """A non-interactive stop. The outer agent reads `data`, answers the question, and calls `run` again."""

    def __init__(self, code: str, message: str, data: dict[str, object]) -> None:
        super().__init__(code, message)
        self.data: dict[str, object] = {"stop": code, "message": message} | data


def detail(text: str | None) -> dict[str, object]:
    """Return the optional `detail` entry of a stop."""
    return {} if text is None else {"detail": text}


def baseline_rejected(text: str | None = None) -> Stop:
    return Stop("baseline-contract-rejected", "original candidate fails the frozen native/helper contract",
                {"next": "fix the skill or its contract, then start a new run in a new directory"} | detail(text))


_CHECK_TIMEOUT_CODES = {"baseline": "baseline-check-timeout", "winner": "winner-check-timeout"}


def check_timeout(arm: str, text: str | None = None) -> Stop:
    """Return the resumable stop for a contract check of `arm` that reached the sandbox time limit twice."""
    return Stop(_CHECK_TIMEOUT_CODES[arm], f"the {arm} contract check reached the sandbox time limit twice",
                {"next": "resume the run in the same directory; a resume retries the check"} | detail(text))
