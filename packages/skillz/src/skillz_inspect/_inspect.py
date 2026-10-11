"""Report package facts without grading the skill."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCHEMA_VERSION = 4
ADVISORY_WORDS = 20
MAX_WORDS = 25
MAX_BYTES = 262144

FRONTMATTER_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):")
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^\s)]+)\)")

# Line shapes for prose scanning.
QUOTE_PREFIX = re.compile(r"^(?: {0,3}> ?)+")
MARKER = re.compile(r"^\s*(?:#+\s+|[-*+]\s+|\d+[.)]\s+|>\s*)")  # Heading, bullet, number, or quote prefix.
BREAK = re.compile(r"^\s*(?:#|[-*+]\s|\d+[.)]\s)")  # A line that starts a new paragraph.
ITEM = re.compile(r"^ *(?:[-*+]|\d+[.)])[ \t]+(?=\S)")  # A list item; its end is the content indent.
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")

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


def _paragraphs(lines: list[str], start: int) -> list[_Paragraph]:
    """Join the prose lines from index `start` into paragraphs. Skip code fences and tables."""
    paragraphs = [_Paragraph()]
    fence = ""  # Opening marker while inside a code fence.
    fence_column = 0  # Content indent of the list item that holds the open fence.
    column = 0  # Content indent of the open list item.

    def end_paragraph() -> None:
        if paragraphs[-1].text:
            paragraphs.append(_Paragraph())

    for number, line in enumerate(lines[start:], start + 1):
        line = QUOTE_PREFIX.sub("", line)
        stripped = line.strip()
        if fence:
            closer = FENCE_CLOSE.match(_unindent(line, fence_column))
            if closer and closer[1][0] == fence[0] and len(closer[1]) >= len(fence):
                fence = ""
            continue
        item = ITEM.match(line)
        if item:
            column = item.end()
        elif stripped and len(line) - len(line.lstrip(" ")) < column:
            end_paragraph()  # Dedent leaves the list item.
            column = 0
        opener = FENCE_OPEN.match(_unindent(line, column))
        if opener and not (opener[1][0] == "`" and "`" in opener[2]):
            end_paragraph()
            fence = opener[1]
            fence_column = column
            continue
        if not stripped or stripped.startswith("|"):  # Blank line or table row.
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


def _local_links(package: Path, body: str) -> set[str]:
    """Collect relative link targets. Reject a target that escapes `package` or crosses a symlink."""
    links: set[str] = set()
    for match in MARKDOWN_LINK.finditer(body):
        parsed = urlsplit(match[1])
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
    path = path.parent.resolve() / path.name
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"input must be a regular file of at most {MAX_BYTES} bytes")
    lines = path.read_text(encoding="utf-8").splitlines()
    end = _frontmatter_end(lines)
    body = lines[end + 1:]
    keys = sorted({match[1] for line in lines[1:end] if (match := FRONTMATTER_KEY.match(line))})
    links = _local_links(path.parent, "\n".join(body))
    hits = prose_sentences(lines, end + 1)
    return {"schema_version": SCHEMA_VERSION, "frontmatter_keys": keys,
            "advisory_sentences": [hit for hit in hits if hit["words"] <= MAX_WORDS],
            "body_line_count": len(body), "local_link_targets": sorted(links),
            "long_sentences": [hit for hit in hits if hit["words"] > MAX_WORDS]}
