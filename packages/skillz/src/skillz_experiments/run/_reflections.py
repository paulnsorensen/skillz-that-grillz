"""The audit log of reflection calls: each prompt and answer, redacted and bounded, in one private file per call.

The log is for audit only. No run decision reads it, and the export leaves it out.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast, final

from skillz_experiments._cases import CodedError
from skillz_experiments._records import write_bytes
from skillz_experiments._redact import redact

REFLECTIONS = "reflections"
# The largest serialized record, in bytes, markers included.
RECORD_LIMIT = 64 * 1024
TRUNCATED = "[truncated]"


def _redacted(value: object) -> object:
    """Redact each string, key and value, inside `value`. Other non-JSON objects pass through `str` first."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {redact(str(key)): _redacted(item) for key, item in cast(dict[object, object], value).items()}
    if isinstance(value, list | tuple):
        return [_redacted(item) for item in cast(list[object] | tuple[object, ...], value)]
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact(str(value))


def _encoded(record: dict[str, object]) -> bytes:
    return (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _fitted(ordinal: int, prompt: str, answer: str) -> bytes:
    """Serialize the record. Cut the longer text, with a `[truncated]` marker, until the record fits `RECORD_LIMIT`."""
    texts = {"prompt": prompt, "answer": answer}
    while len(data := _encoded({"ordinal": ordinal, **texts})) > RECORD_LIMIT:
        name = max(texts, key=lambda key: len(texts[key]))
        text = texts[name].removesuffix(TRUNCATED)
        # Each character takes at least one byte, so a cut of `excess` characters frees at least `excess` bytes.
        texts[name] = text[:max(0, len(text) - (len(data) - RECORD_LIMIT))] + TRUNCATED
    return data


@final
class ReflectionLog:
    """Write `<run>/reflections/<ordinal>.json` for each reflection call. A record never replaces another."""

    def __init__(self, run_dir: Path) -> None:
        self._directory = run_dir / REFLECTIONS

    def record(self, ordinal: int, prompt: str, answer: object) -> None:
        """Write the redacted `prompt` and `answer` (as JSON text) of one reflection call with mode 0600.

        Raise `reflection-record-exists` when the file is already there, and `reflection-record-failed` on a write error.
        """
        path = self._directory / f"{ordinal}.json"
        # Redact every string first: a cut before redaction can drop the closing quote of a credential value, and
        # JSON encoding adds backslashes that hide an escaped quote.
        answer_text = json.dumps(_redacted(answer), sort_keys=True, default=str)
        data = _fitted(ordinal, redact(prompt), answer_text)
        try:
            if self._directory.is_symlink():
                raise OSError(f"{self._directory} is a symlink")
            self._directory.mkdir(mode=0o700, exist_ok=True)
            written = not path.exists() and write_bytes(path, data, guard=lambda: not path.exists())
        except OSError as error:
            raise CodedError("reflection-record-failed", f"the reflection record {path} cannot be written: {error}; "
                             + "fix the cause, then resume the run in the same directory") from error
        if not written:
            raise CodedError("reflection-record-exists", f"the reflection record {path} already exists; move it out of "
                             + "the run directory, then resume the run")
