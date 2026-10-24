"""The one redaction seam for diagnostic text that the runner keeps or shows.

Stderr excerpts, error messages, and event transcripts pass through `redact`, so one pattern decides what counts as
a credential. `write_transcript` writes a redacted private transcript, such as the record of a failed preflight.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from skillz_experiments._records import write

_FLAGS = re.IGNORECASE | re.DOTALL
_KEYWORDS = r"api[-_ ]?key|key|token|secret|password|passwd|authorization|auth|cookie"
_PAIR_WORDS = re.compile(r"key|token|secret|password|passwd|auth|cookie", re.IGNORECASE)
_SIMPLE_WORDS = re.compile(r"key|token|secret|password|auth", re.IGNORECASE)
# A quoted value keeps all of its words, across lines: a JSON string, a single-quoted string, or a JSON-escaped
# `\"...\"` string. A quote with no closing quote runs to the end of the input.
_QUOTED = (r'"(?:[^"\\]++|\\.)*+(?:"|\\?\Z)|' + r"'(?:[^'\\]++|\\.)*+(?:'|\\?\Z)|" + r'\\"[\s\S]*?(?:\\"|\Z)')
_VALUE = r"(?:(?:basic|bearer)\s++)?(?:" + _QUOTED + r"|\S+)"
# A credential word that ends a word, with its value: `password: "..."`, `my_api_key abc`.
_KEYWORD_HEAD = r"(?<![\w-])(?:\w+-)*(?:\w+_)?(?:" + _KEYWORDS + r")s?(?!\w)"
_KEYWORD_START = re.compile(_KEYWORD_HEAD + r"(?=\s*+\S)", _FLAGS)
_KEYWORD_SPAN = re.compile(_KEYWORD_HEAD + r"\s*+[:=]?\s*+" + _VALUE, _FLAGS)
# A credential-named key inside a longer or quoted name, with its value: `{"refreshToken": "..."`.
_RUN = re.compile(r"[^\s:=]+")
_DELIMITER = re.compile(r"\s*+[:=]")
_TRAILING_WORD = re.compile(r"[\w-]*+")
_PAIR_TAIL = re.compile(r"\s*+[:=]\s*+" + _VALUE, _FLAGS)
_SK = re.compile(r"\bsk-\S+", re.IGNORECASE)
_BEARER = re.compile(r"\bbearer\s++\S+", re.IGNORECASE)
_BASIC = re.compile(r"\bbasic\s++\S+", re.IGNORECASE)
_JWT_ANCHOR = re.compile(r"\beyj", re.IGNORECASE)
_JWT_WORD = re.compile(r"[\w-]*+")
_JWT_TAIL = re.compile(r"\.[\w-]++\.[\w-]*+")
_JWT = re.compile(r"\beyj[\w-]++\.[\w-]++\.[\w-]*+", re.IGNORECASE)
_SPACE = re.compile(r"\s")
_CREDENTIAL_KEY = re.compile(r"key|token|secret|password|passwd|auth|cookie", re.IGNORECASE)
_NO_HIT = 1 << 62


def _find_url(text: str, pos: int) -> int | None:
    found = text.find("://", pos)
    return None if found < 0 else found


def _find_with(pattern: re.Pattern[str]) -> Callable[[str, int], int | None]:
    def find(text: str, pos: int) -> int | None:
        hit = pattern.search(text, pos)
        return None if hit is None else hit.start()

    return find


def _find_pair(text: str, pos: int) -> int | None:
    """The start of the next run of non-separator characters that ends in a credential-named key and a `:` or `=`."""
    while (run := _RUN.search(text, pos)) is not None:
        start, end = run.span()
        pos = end
        delimiter = _DELIMITER.match(text, end)
        if delimiter is None or delimiter.end() >= len(text):
            continue
        key_end = end - 1 if text[end - 1] in "\"'" else end
        if key_end > start and text[key_end - 1] == "\\":
            key_end -= 1
        word = _TRAILING_WORD.match(text[start:key_end][::-1])
        word_start = key_end - (0 if word is None else word.end())
        if _PAIR_WORDS.search(text, word_start, key_end) is not None:
            return start
    return None


def _find_jwt(text: str, pos: int) -> int | None:
    """A failed token fails every `eyj` inside its word, so the scan jumps past that word."""
    while (anchor := _JWT_ANCHOR.search(text, pos)) is not None:
        word = _JWT_WORD.match(text, anchor.end())
        word_end = anchor.end() if word is None else word.end()
        if word_end > anchor.end() and _JWT_TAIL.match(text, word_end) is not None:
            return anchor.start()
        pos = word_end
    return None


_URL, _KEYWORD, _PAIR, _SIMPLE = 0, 1, 2, 3
_FINDERS: tuple[Callable[[str, int], int | None], ...] = (
    _find_url, _find_with(_KEYWORD_START), _find_pair, _find_with(_SIMPLE_WORDS), _find_with(_SK),
    _find_with(_BEARER), _find_with(_BASIC), _find_jwt)
_SPANS = {_KEYWORD: _KEYWORD_SPAN, 4: _SK, 5: _BEARER, 6: _BASIC, 7: _JWT}


def redact(text: str) -> str:
    """Strip `text`, then replace each URL and each credential-like word or value with `[redacted]`.

    One forward pass: each alternative keeps its next hit, the hit that starts first wins, and the scan resumes at its
    end. No text is scanned twice, so the cost grows linearly with the length, whatever the text holds.
    """
    text = text.strip()
    spaces = [hit.start() for hit in _SPACE.finditer(text)]
    length = len(text)

    def token_start(anchor: int) -> int:
        before = bisect_left(spaces, anchor)
        return spaces[before - 1] + 1 if before else 0

    def token_end(anchor: int) -> int:
        after = bisect_right(spaces, anchor)
        return spaces[after] if after < len(spaces) else length

    def span_end(kind: int, anchor: int) -> int:
        if kind in (_URL, _SIMPLE):
            return token_end(anchor)
        if kind == _PAIR:
            run = _RUN.match(text, anchor)
            tail = None if run is None else _PAIR_TAIL.match(text, run.end())
        else:
            tail = _SPANS[kind].match(text, anchor)
        return token_end(anchor) if tail is None else tail.end()

    anchors = [-1] * len(_FINDERS)
    pieces: list[str] = []
    pos = 0
    while pos < length:
        best: tuple[int, int, int] | None = None
        for kind, find in enumerate(_FINDERS):
            if anchors[kind] < pos:
                found = find(text, pos)
                anchors[kind] = _NO_HIT if found is None else found
            anchor = anchors[kind]
            if anchor == _NO_HIT:
                continue
            start = max(pos, token_start(anchor)) if kind in (_URL, _SIMPLE) else anchor
            if best is None or start < best[0]:
                best = (start, kind, anchor)
        if best is None:
            break
        start, kind, anchor = best
        pieces.append(text[pos:start])
        pieces.append("[redacted]")
        pos = max(span_end(kind, anchor), start + 1)
    pieces.append(text[pos:])
    return "".join(pieces)


def _redacted(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {str(key): "[redacted]" if _CREDENTIAL_KEY.search(str(key)) else _redacted(item)
                for key, item in cast(Mapping[object, object], value).items()}
    if isinstance(value, list | tuple):
        return [_redacted(item) for item in cast(list[object], value)]
    return value


def write_transcript(path: Path, transcript: Mapping[str, object]) -> None:
    """Write `transcript` to `path` as private JSON (mode 0600). Keys stay. Each string value passes through `redact`,
    and the whole value of a credential-named key, such as `api_key` or `Authorization`, becomes `[redacted]`."""
    write(path, _redacted(transcript))
