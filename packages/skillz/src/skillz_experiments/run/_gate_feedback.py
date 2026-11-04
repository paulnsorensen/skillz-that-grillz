"""Cut a gate diagnostic to the proposer feedback size."""
from __future__ import annotations

from skillz_experiments._graders import DIAGNOSTIC_LIMIT
from skillz_experiments.search import HarnessText

_STDOUT, _STDERR = "stdout: ", "stderr: "


def _split(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split the lead line from the labelled tails that `_graders.command` writes after it.

    The lead is one line, so a tail label starts the second line. The stdout tail ends at the last `stderr: ` line,
    because stdout can hold a label line and the stderr tail comes last. Text without a label gives no tails.
    """
    lead, _, rest = text.partition("\n")
    if rest.startswith(_STDERR):
        return lead, [("stderr", rest[len(_STDERR):])]
    if not rest.startswith(_STDOUT):
        return text, []
    body = rest[len(_STDOUT):]
    cut = body.rfind(f"\n{_STDERR}")
    if cut < 0:
        return lead, [("stdout", body)]
    return lead, [("stdout", body[:cut]), ("stderr", body[cut + 1 + len(_STDERR):])]


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
