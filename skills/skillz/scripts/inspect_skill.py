#!/usr/bin/env python3
"""Report package facts without grading the skill."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = 3
ADVISORY_WORDS = 20
MAX_WORDS = 25
MARKER = re.compile(r"^\s*(?:#+\s+|[-*+]\s+|\d+[.)]\s+|>\s*)")
BREAK = re.compile(r"^\s*(?:#|[-*+]\s|\d+[.)]\s)")
MASK = re.compile(r"`[^`]*`|\"[^\"]*\"|“[^”]*”|(?<=\])\([^)]*\)")
SENTENCE = re.compile(r"\S.*?(?:[.!?]+(?=\s|$)|$)", re.S)
OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
ITEM = re.compile(r"^ *(?:[-*+]|\d+[.)])[ \t]+(?=\S)")


def _unindent(line: str, column: int) -> str:
    """Remove up to `column` leading spaces: the content indent of the open list item."""
    return line[min(column, len(line) - len(line.lstrip(" "))):]


def prose_sentences(lines: list[str], start: int) -> list[dict[str, int]]:
    """Report prose sentences with more than ADVISORY_WORDS words, by 1-based file line."""
    paragraphs: list[tuple[str, list[int]]] = []
    text = ""
    owners: list[int] = []
    fence = ""

    def flush() -> None:
        nonlocal text, owners
        if text:
            paragraphs.append((text, owners))
        text = ""
        owners = []

    column = 0
    fence_column = 0
    for number, line in enumerate(lines[start:], start + 1):
        line = re.sub(r"^(?: {0,3}> ?)+", "", line)
        stripped = line.strip()
        if fence:
            closer = CLOSE.match(_unindent(line, fence_column))
            if closer and closer[1][0] == fence[0] and len(closer[1]) >= len(fence):
                fence = ""
            continue
        item = ITEM.match(line)
        if item:
            column = item.end()
        elif stripped and len(line) - len(line.lstrip(" ")) < column:
            flush()
            column = 0
        opener = OPEN.match(_unindent(line, column))
        if opener and not (opener[1][0] == "`" and "`" in opener[2]):
            flush()
            fence = opener[1]
            fence_column = column
            continue
        if not stripped or stripped.startswith("|"):
            flush()
            continue
        if BREAK.match(line):
            flush()
        piece = MARKER.sub("", line).strip()
        if text:
            piece = " " + piece
        text += piece
        owners += [number] * len(piece)
    flush()
    found: list[dict[str, int]] = []
    for text, owners in paragraphs:
        masked = MASK.sub(lambda hit: " " * len(hit[0]), text)
        for sentence in SENTENCE.finditer(masked):
            words = sum(1 for token in sentence[0].split() if any(c.isalnum() for c in token))
            if words > ADVISORY_WORDS:
                found.append({"line": owners[sentence.start()], "words": words})
    return found


def long_sentences(lines: list[str], start: int) -> list[dict[str, int]]:
    """Hard tier: sentences with more than MAX_WORDS words."""
    return [hit for hit in prose_sentences(lines, start) if hit["words"] > MAX_WORDS]


def advisory_sentences(lines: list[str], start: int) -> list[dict[str, int]]:
    """Advisory tier: sentences with more than ADVISORY_WORDS and at most MAX_WORDS words."""
    return [hit for hit in prose_sentences(lines, start) if hit["words"] <= MAX_WORDS]


def inspect(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise ValueError("input must not be a symlink")
    path = path.parent.resolve() / path.name
    if not path.is_file() or path.stat().st_size > 262144:
        raise ValueError("input must be a regular file of at most 262144 bytes")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("file needs frontmatter delimiters")
    end = lines.index("---", 1)
    keys = sorted({match[1] for line in lines[1:end]
                   if (match := re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):", line))})
    links: set[str] = set()
    for match in re.finditer(r"\[[^\]]*\]\(([^\s)]+)\)", "\n".join(lines[end + 1:])):
        parsed = urlsplit(match[1])
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        target = unquote(parsed.path)
        destination = path.parent / target
        if not destination.resolve().is_relative_to(path.parent.resolve()):
            raise ValueError("link escapes package")
        if any(parent.is_symlink() for parent in (destination, *destination.parents)
               if parent.is_relative_to(path.parent)):
            raise ValueError("package must not contain symlinks")
        links.add(target)
    return {"schema_version": SCHEMA_VERSION, "frontmatter_keys": keys,
            "advisory_sentences": advisory_sentences(lines, end + 1),
            "body_line_count": len(lines[end + 1:]), "local_link_targets": sorted(links),
            "long_sentences": long_sentences(lines, end + 1)}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1:
            raise ValueError("usage: inspect_skill.py PATH")
        result = inspect(Path(args[0]))
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
