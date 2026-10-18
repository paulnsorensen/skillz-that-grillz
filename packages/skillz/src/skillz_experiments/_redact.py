"""The one redaction seam for diagnostic text that the runner keeps or shows.

Stderr excerpts, error messages, and event transcripts pass through `redact`, so one pattern decides what counts as
a credential. `write_transcript` writes a redacted private transcript, such as the record of a failed preflight.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from skillz_experiments._records import write

_KEYWORD = r"(?<![\w-])(?:\w+[-_])*(?:api[-_ ]?key|key|token|secret|password|passwd|authorization|auth|cookie)s?(?!\w)"
# A credential-named key inside a longer or quoted name, with its value: `{"refreshToken": "..."`.
_PAIR = (r"[^\s:=]*(?:key|token|secret|password|passwd|auth|cookie)[\w-]*[\"']?\s*[:=]\s*"
         + r"(?:(?:basic|bearer)\s+)?[\"']?[^\s\"',}]+[\"']?")
SECRETISH = re.compile(
    r"\S*://\S*|" + _KEYWORD + r"\s*[:=]?\s*(?:(?:basic|bearer)\s+)?\S+|" + _PAIR + "|"
    + r"\S*(?:key|token|secret|password|auth)\S*|\bsk-\S+|\bbearer\s+\S+|\bbasic\s+\S+|\beyj[\w-]+\.[\w-]+\.[\w-]*",
    re.IGNORECASE)


def redact(text: str) -> str:
    """Strip `text`, then replace each URL and each credential-like word or value with `[redacted]`."""
    return SECRETISH.sub("[redacted]", text.strip())


def _redacted(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {str(key): _redacted(item) for key, item in cast(Mapping[object, object], value).items()}
    if isinstance(value, list | tuple):
        return [_redacted(item) for item in cast(list[object], value)]
    return value


def write_transcript(path: Path, transcript: Mapping[str, object]) -> None:
    """Write `transcript` to `path` as private JSON (mode 0600). Redact each string value first; keys stay."""
    write(path, _redacted(transcript))
