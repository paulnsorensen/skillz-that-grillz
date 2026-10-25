"""Report package facts without grading the skill."""

from __future__ import annotations

import os
import re
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCHEMA_VERSION = 4
ADVISORY_WORDS = 20
MAX_WORDS = 25
MAX_BYTES = 262144

FRONTMATTER_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):")
# An inline link: a bare or `<...>` destination, then an optional "title", 'title', or (title).
# A title allows backslash escapes and never spans a blank line, so it cannot hide a later link.
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(\s*(?:<(?P<angle>[^>\n]*)>|(?P<bare>[^\s)]+))"
                           + r"(?:\s+(?:\"(?:[^\"\\\n]|\\.|\n(?![ \t]*\n))*\""
                           + r"|'(?:[^'\\\n]|\\.|\n(?![ \t]*\n))*'"
                           + r"|\((?:[^()\\\n]|\\.|\n(?![ \t]*\n))*\)))?\s*\)")

# Line shapes for prose scanning.
QUOTE_PREFIX = re.compile(r"^(?: {0,3}> ?)+")
MARKER = re.compile(r"^\s*(?:#+\s+|[-*+]\s+|\d+[.)]\s+|>\s*)")  # Heading, bullet, number, or quote prefix.
BREAK = re.compile(r"^\s*(?:#|[-*+]\s|\d+[.)]\s)")  # A line that starts a new paragraph.
ITEM = re.compile(r"^ *(?:[-*+]|\d+[.)])[ \t]+(?=\S)")  # A list item; its end is the content indent.
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
BACKTICKS = re.compile(r"`+")

# Text shapes for sentence counting.
MASK = re.compile(r"`[^`]*`|\"[^\"]*\"|“[^”]*”|(?<=\])\([^)]*\)")  # Code spans, quotes, and link targets.
SENTENCE = re.compile(r"\S.*?(?:[.!?]+(?=\s|$)|$)", re.S)


@dataclass
class _Paragraph:
    """Joined prose text; `owners[i]` is the 1-based file line of `text[i]`."""

    text: str = ""
    owners: list[int] = field(default_factory=list)

    def add(self, piece: str, line_number: int) -> None:
        if self.text:
            piece = " " + piece
        self.text += piece
        self.owners += [line_number] * len(piece)


def _unindent(line: str, column: int) -> str:
    """Remove up to `column` leading spaces: the content indent of the open list item."""
    return line[min(column, len(line) - len(line.lstrip(" "))):]


def _body_lines(lines: list[str], start: int) -> Iterator[tuple[int, str, str]]:
    """Yield the 1-based number, the line without its quote prefix, and the kind of each line from index `start`.

    The kind is "code" inside a code fence or on its closing line, "fence" on an opening fence,
    "dedent" on a line that leaves the open list item, and "text" otherwise.
    """
    fence = ""  # Opening marker while inside a code fence.
    fence_column = 0  # Content indent of the list item that holds the open fence.
    column = 0  # Content indent of the open list item.
    for number, line in enumerate(lines[start:], start + 1):
        line = QUOTE_PREFIX.sub("", line)
        if fence:
            closer = FENCE_CLOSE.match(_unindent(line, fence_column))
            if closer and closer[1][0] == fence[0] and len(closer[1]) >= len(fence):
                fence = ""
            yield number, line, "code"
            continue
        kind = "text"
        item = ITEM.match(line)
        if item:
            column = item.end()
        elif line.strip() and len(line) - len(line.lstrip(" ")) < column:
            kind = "dedent"
            column = 0
        opener = FENCE_OPEN.match(_unindent(line, column))
        if opener and not (opener[1][0] == "`" and "`" in opener[2]):
            fence, fence_column, kind = opener[1], column, "fence"
        yield number, line, kind


def _paragraphs(lines: list[str], start: int) -> list[_Paragraph]:
    """Join the prose lines from index `start` into paragraphs. Skip code fences and tables."""
    paragraphs = [_Paragraph()]

    def end_paragraph() -> None:
        if paragraphs[-1].text:
            paragraphs.append(_Paragraph())

    for number, line, kind in _body_lines(lines, start):
        if kind == "code":
            continue
        if kind != "text":  # A dedent leaves the list item, or a code fence opens.
            end_paragraph()
        stripped = line.strip()
        if kind == "fence" or not stripped or stripped.startswith("|"):  # Code fence, blank line, or table row.
            end_paragraph()
            continue
        if BREAK.match(line):
            end_paragraph()
        paragraphs[-1].add(MARKER.sub("", line).strip(), number)
    return [paragraph for paragraph in paragraphs if paragraph.text]


def _word_count(sentence: str) -> int:
    return sum(1 for token in sentence.split() if any(char.isalnum() for char in token))


def prose_sentences(lines: list[str], start: int) -> list[dict[str, int]]:
    """Report prose sentences with more than ADVISORY_WORDS words, by 1-based file line."""
    found: list[dict[str, int]] = []
    for paragraph in _paragraphs(lines, start):
        masked = MASK.sub(lambda hit: " " * len(hit[0]), paragraph.text)
        for sentence in SENTENCE.finditer(masked):
            words = _word_count(sentence[0])
            if words > ADVISORY_WORDS:
                found.append({"line": paragraph.owners[sentence.start()], "words": words})
    return found


def _frontmatter_end(lines: list[str]) -> int:
    """Return the index of the line that closes the frontmatter."""
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("file needs frontmatter delimiters")
    return lines.index("---", 1)


def _code_spans(paragraph: str) -> list[tuple[int, int]]:
    """Return the code spans of one paragraph: a backtick run up to the next run of the same length.

    A run after a backslash does not open a span. Each run is looked up once, so the scan stays near-linear.
    """
    runs = list(BACKTICKS.finditer(paragraph))
    by_length: dict[int, list[int]] = {}
    for index, run in enumerate(runs):
        by_length.setdefault(len(run[0]), []).append(index)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(runs):
        run = runs[index]
        same = by_length[len(run[0])]
        later = bisect_right(same, index)
        if paragraph[run.start() - 1:run.start()] != "\\" and later < len(same):
            spans.append((run.start(), runs[same[later]].end()))
            index = same[later]
        index += 1
    return spans


def _code_free_body(lines: list[str], start: int) -> str:
    """Join the lines from index `start`. Blank code fences and mask code spans, so a sample link in code is no link.

    A code span is inline, so it never crosses a blank line, a heading, a list item, or a table row.
    """
    segments: list[list[str]] = []
    for _number, line, kind in _body_lines(lines, start):
        line = "" if kind in ("code", "fence") else line
        boundary = not line.strip() or bool(BREAK.match(line)) or line.lstrip().startswith("|")
        single = bool(segments) and segments[-1][0].lstrip().startswith(("#", "|"))  # A heading or row is one line.
        if boundary or single or not segments or not segments[-1][0].strip():
            segments.append([])
        segments[-1].append(line)
    masked: list[str] = []
    for segment in segments:
        paragraph = "\n".join(segment)
        pieces: list[str] = []
        last = 0
        for begin, end in _code_spans(paragraph):
            pieces += [paragraph[last:begin], " " * (end - begin)]
            last = end
        masked.append("".join(pieces) + paragraph[last:])
    return "\n".join(masked)


def _local_links(package: Path, body: str) -> set[str]:
    """Collect relative link targets. Reject a target that escapes `package` or crosses a symlink."""
    links: set[str] = set()
    for match in MARKDOWN_LINK.finditer(body):
        parsed = urlsplit(match["bare"] or match["angle"])
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = unquote(parsed.path)
        destination = package / target
        # Path.resolve raises RuntimeError on a symlink loop before Python 3.13; realpath does not on any version.
        if not Path(os.path.realpath(destination)).is_relative_to(package):
            raise ValueError("link escapes package")
        if any(parent.is_symlink() for parent in (destination, *destination.parents)
               if parent.is_relative_to(package)):
            raise ValueError("package must not contain symlinks")
        links.add(target)
    return links


def inspect(path: Path) -> dict[str, object]:
    """Report the package facts of one Markdown file. Raise `ValueError` for input outside the contract."""
    if path.is_symlink():
        raise ValueError("input must not be a symlink")
    # Path.resolve raises RuntimeError on a symlink loop before Python 3.13; realpath does not on any version.
    path = Path(os.path.realpath(path.parent)) / path.name
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"input must be a regular file of at most {MAX_BYTES} bytes")
    lines = path.read_text(encoding="utf-8").splitlines()
    end = _frontmatter_end(lines)
    body = lines[end + 1:]
    keys = sorted({match[1] for line in lines[1:end] if (match := FRONTMATTER_KEY.match(line))})
    links = _local_links(path.parent, _code_free_body(lines, end + 1))
    hits = prose_sentences(lines, end + 1)
    return {"schema_version": SCHEMA_VERSION, "frontmatter_keys": keys,
            "advisory_sentences": [hit for hit in hits if hit["words"] <= MAX_WORDS],
            "body_line_count": len(body), "local_link_targets": sorted(links),
            "long_sentences": [hit for hit in hits if hit["words"] > MAX_WORDS]}
