"""Cut a gate diagnostic to the proposer feedback size."""
from __future__ import annotations

import re

from skillz_experiments._graders import DIAGNOSTIC_LIMIT
from skillz_experiments.search import HarnessText

_TAIL_START = re.compile(r"\n(?=(?:stdout|stderr): )")


def gate_feedback(diagnostic: str, harness: HarnessText) -> str:
    """Mask the diagnostic and cut it to `DIAGNOSTIC_LIMIT`: keep the lead line, and cut each labelled tail from its start."""
    text = harness.mask(diagnostic)
    lead, *tails = _TAIL_START.split(text)
    if not tails or len(text) <= DIAGNOSTIC_LIMIT:
        return text[-DIAGNOSTIC_LIMIT:]
    lead = lead[:DIAGNOSTIC_LIMIT // 4]
    share = (DIAGNOSTIC_LIMIT - len(lead) - len(tails)) // len(tails)
    kept: list[str] = []
    for tail in tails:
        label, _, body = tail.partition(": ")
        kept.append(f"{label}: {body[-(share - len(label) - 2):]}")
    return "\n".join([lead, *kept])
