"""Cut a gate diagnostic to the proposer feedback size."""
from __future__ import annotations

from skillz_experiments._graders import DIAGNOSTIC_LIMIT
from skillz_experiments.search import HarnessText

_LABELS = ("stdout", "stderr")


def _split(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split at the first `stdout: ` line and the first later `stderr: ` line: the two labels `_graders.command` writes."""
    starts: list[tuple[str, int]] = []
    position = 0
    for label in _LABELS:
        found = text.find(f"\n{label}: ", position)
        if found >= 0:
            starts.append((label, found))
            position = found + len(label) + 3
    ends = [start for _, start in starts[1:]] + [len(text)]
    return text[:starts[0][1]] if starts else text, [
        (label, text[start + len(label) + 3:end]) for (label, start), end in zip(starts, ends)]


def gate_feedback(diagnostic: str, harness: HarnessText) -> str:
    """Mask the diagnostic and cut it to `DIAGNOSTIC_LIMIT`: keep the lead line, and cut each labelled tail from its start.

    A tail keeps at most an equal share; spare space from a short tail goes to the other.
    """
    text = harness.mask(diagnostic)
    lead, tails = _split(text)
    if not tails or len(text) <= DIAGNOSTIC_LIMIT:
        return text[-DIAGNOSTIC_LIMIT:]
    lead = lead[:DIAGNOSTIC_LIMIT // 4]
    room = DIAGNOSTIC_LIMIT - len(lead) - sum(len(label) + 3 for label, _ in tails)
    keep: dict[str, int] = {}
    for remaining, (label, body) in enumerate(sorted(tails, key=lambda tail: len(tail[1])), start=1):
        keep[label] = min(len(body), max(room // (len(tails) - remaining + 1), 0))
        room -= keep[label]
    return "\n".join([lead, *(f"{label}: {body[len(body) - keep[label]:]}" for label, body in tails)])
