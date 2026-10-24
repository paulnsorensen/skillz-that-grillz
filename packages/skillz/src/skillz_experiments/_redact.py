"""The one redaction seam for diagnostic text that the runner keeps or shows.

Stderr excerpts, error messages, and event transcripts pass through `redact`, so one pattern decides what counts as
a credential. `write_transcript` writes a redacted private transcript, such as the record of a failed preflight.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import cast, final

from skillz_experiments._records import write

_FLAGS = re.IGNORECASE | re.DOTALL
_KEYWORDS = r"api[-_ ]?key|key|token|secret|password|passwd|authorization|auth|cookie"
_SIMPLE_WORDS = re.compile(r"key|token|secret|password|auth", re.IGNORECASE)
# A quoted value keeps all of its words, across lines: a JSON string, a single-quoted string, or a JSON-escaped
# `\"...\"` string. A quote with no closing quote runs to the end of the input. This pattern covers a single quote
# after whitespace or a separator, which always opens a value. A single quote that directly follows a credential key
# is handled by `redact`: `_Quotes` decides if it is a prose apostrophe or the start of a value.
_QUOTED = (r'"(?:[^"\\]++|\\.)*+(?:"|\\?\Z)|'
           + r"(?<=[\s:=])'(?:[^'\\]++|\\.)*+(?:'|\\?\Z)|"
           + r'\\"[\s\S]*?(?:\\"|\Z)')
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
_PAIR_WORD_AT = re.compile(r"(?=key|token|secret|password|passwd|auth|cookie)", re.IGNORECASE)
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


@final
class _Pairs:
    """Finds the next run of non-separator characters that ends in a credential-named key and a `:` or `=`.

    The scan moves forward only. Each run is measured once, when the cursor reaches it, so a run that holds many
    redactions is not scanned again for each of them.
    """

    def __init__(self, text: str) -> None:
        self._text: str = text
        self._runs: Iterator[re.Match[str]] = _RUN.finditer(text)
        self._run: re.Match[str] | None = next(self._runs, None)
        self._info: tuple[int, int, int] | None = None
        self._measured: bool = False
        self.hit_end: int = 0

    def _advance(self) -> None:
        self._run = next(self._runs, None)
        self._measured = False

    def _measure(self, start: int, end: int) -> tuple[int, int, int] | None:
        """The end of the key, the start of its trailing word, and the last credential word inside that word."""
        text = self._text
        delimiter = _DELIMITER.match(text, end)
        if delimiter is None or delimiter.end() >= len(text):
            return None
        key_end = end - 1 if text[end - 1] in "\"'" else end
        if key_end > start and text[key_end - 1] == "\\":
            key_end -= 1
        word = _TRAILING_WORD.match(text[start:key_end][::-1])
        word_start = key_end - (0 if word is None else word.end())
        last = -1
        for hit in _PAIR_WORD_AT.finditer(text, word_start, key_end):
            last = hit.start()
        return key_end, word_start, last

    def find(self, _text: str, pos: int) -> int | None:
        while self._run is not None:
            run_start, run_end = self._run.span()
            if run_end <= pos:
                self._advance()
                continue
            if not self._measured:
                self._info = self._measure(run_start, run_end)
                self._measured = True
            start = max(run_start, pos)
            if self._info is not None:
                key_end, word_start, last = self._info
                if key_end > start and last >= max(word_start, start):
                    self.hit_end = run_end
                    return start
            self._advance()
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
_SPANS = {_KEYWORD: _KEYWORD_SPAN, 4: _SK, 5: _BEARER, 6: _BASIC, 7: _JWT}


def redact(text: str) -> str:
    """Strip `text`, then replace each URL and each credential-like word or value with `[redacted]`.

    One forward pass: each alternative keeps its next hit, the hit that starts first wins, and the scan resumes at its
    end. Whitespace and run boundaries are found with forward cursors, so the cost grows linearly with the length.
    """
    text = text.strip()
    spaces = [hit.start() for hit in _SPACE.finditer(text)]
    length = len(text)
    last_quote = text.rfind("'")
    pairs = _Pairs(text)
    finders: tuple[Callable[[str, int], int | None], ...] = (
        _find_url, _find_with(_KEYWORD_START), pairs.find, _find_with(_SIMPLE_WORDS), _find_with(_SK),
        _find_with(_BEARER), _find_with(_BASIC), _find_jwt)
    cursors = [0] * len(finders)

    def token_bounds(kind: int, anchor: int) -> tuple[int, int]:
        """The whitespace-delimited token around `anchor`; the anchors of one kind only move forward."""
        before = cursors[kind]
        while before < len(spaces) and spaces[before] < anchor:
            before += 1
        cursors[kind] = before
        after = before + 1 if before < len(spaces) and spaces[before] == anchor else before
        return (spaces[before - 1] + 1 if before else 0), (spaces[after] if after < len(spaces) else length)

    def quote_end(key: str, opening: int) -> int:
        """The end of what a single quote that directly follows `key` covers.

        A lowercase key, `'s` and the last quote in the text make a prose apostrophe, so only `'s` is covered.
        Any other quote opens a value that runs to the end of the input.
        """
        after = opening + 2
        if (opening == last_quote and key.isascii() and key.isalpha() and key.islower()
                and text.startswith("s", opening + 1) and (after >= length or text[after].isspace())):
            return after
        return length
    def span_end(kind: int, anchor: int) -> int:
        if kind == _KEYWORD:
            head = _KEYWORD_START.match(text, anchor)
            if head is not None and text.startswith("'", head.end()):
                return quote_end(head.group(), head.end())
        if kind in (_URL, _SIMPLE):
            return token_bounds(kind, anchor)[1]
        tail = _PAIR_TAIL.match(text, pairs.hit_end) if kind == _PAIR else _SPANS[kind].match(text, anchor)
        return token_bounds(kind, anchor)[1] if tail is None else tail.end()

    anchors = [-1] * len(finders)
    pieces: list[str] = []
    pos = 0
    while pos < length:
        best: tuple[int, int, int] | None = None
        for kind, find in enumerate(finders):
            if anchors[kind] < pos:
                found = find(text, pos)
                anchors[kind] = _NO_HIT if found is None else found
            anchor = anchors[kind]
            if anchor == _NO_HIT:
                continue
            start = max(pos, token_bounds(kind, anchor)[0]) if kind in (_URL, _SIMPLE) else anchor
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
