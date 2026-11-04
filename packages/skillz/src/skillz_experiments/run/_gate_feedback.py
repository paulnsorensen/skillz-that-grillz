"""Cut a gate diagnostic to the proposer feedback size."""
from __future__ import annotations

from skillz_experiments._graders import DIAGNOSTIC_LIMIT
from skillz_experiments.search import HarnessText

_STDOUT, _STDERR = "stdout: ", "stderr: "


def _split(text: str, stdout_chars: object) -> tuple[str, list[tuple[str, str]]]:
    """Split the lead line from the labelled tails that `_graders.command` writes after it.

    `stdout_chars` is the stdout tail length that `_graders.command` records. It marks the stream boundary, so a label
    line inside gate output stays in its tail. Text without a valid length or without a label gives no tails.
    """
    lead, _, rest = text.partition("\n")
    if type(stdout_chars) is not int or stdout_chars < 0:
        return text, []
    tails: list[tuple[str, str]] = []
    if stdout_chars and rest.startswith(_STDOUT):
        end = len(_STDOUT) + stdout_chars
        tails, rest = [("stdout", rest[len(_STDOUT):end])], rest[end + 1:]
    if rest.startswith(_STDERR):
        tails.append(("stderr", rest[len(_STDERR):]))
    return (lead, tails) if tails else (text, [])


def gate_feedback(diagnostic: str, stdout_chars: object, harness: HarnessText) -> str:
    """Mask the diagnostic and cut it to `DIAGNOSTIC_LIMIT`: keep the lead line, and cut each labelled tail from its start.

    `stdout_chars` is the recorded `gate_stdout_chars`. A tail keeps at most an equal share; spare space from a short
    tail goes to the other.
    """
    text = harness.mask(diagnostic)
    lead, tails = _split(diagnostic, stdout_chars)
    if not tails or len(text) <= DIAGNOSTIC_LIMIT:
        return text[-DIAGNOSTIC_LIMIT:]
    lead = harness.mask(lead)[:DIAGNOSTIC_LIMIT // 4]
    tails = [(label, harness.mask(body)) for label, body in tails]
    room = DIAGNOSTIC_LIMIT - len(lead) - sum(len(label) + 3 for label, _ in tails)
    keep: dict[str, int] = {}
    for remaining, (label, body) in enumerate(sorted(tails, key=lambda tail: len(tail[1])), start=1):
        keep[label] = min(len(body), max(room // (len(tails) - remaining + 1), 0))
        room -= keep[label]
    return "\n".join([lead, *(f"{label}: {body[len(body) - keep[label]:]}" for label, body in tails)])
