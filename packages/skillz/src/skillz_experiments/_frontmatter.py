"""Read the top-level fields of a SKILL.md or agent frontmatter block, with the standard library only.

Wedge bundles only pure-Python wheels, so a YAML parser cannot ship in the `.pyz` assets. This reader
handles the flat `key: value` shape that these files use and rejects what it cannot read.
"""

from __future__ import annotations

import re

BLOCK_SCALAR = re.compile(r"[|>](?:[+-]?\d?|\d[+-])")
COMMENT = re.compile(r"(?:^|\s)#.*$")


def _unquote(text: str) -> str:
    """Strip one matching pair of quotes and a trailing comment. Raise `ValueError` for a broken quote."""
    text = text.strip()
    if text[:1] in ("'", '"'):
        quote = text[0]
        end = 1
        while end < len(text) and text[end] != quote:
            end += 2 if quote == '"' and text[end] == "\\" else 1
        rest = text[end + 1:]
        if end >= len(text) or (rest.strip() and not rest[:1].isspace() or COMMENT.sub("", rest).strip()):
            raise ValueError("frontmatter has a broken quote")
        return text[1:end]
    text = COMMENT.sub("", text).strip()
    if text[:1] in ("[", "{") and text[-1] != {"[": "]", "{": "}"}[text[0]]:
        raise ValueError("frontmatter has an open collection")
    return text


def read_frontmatter(text: str) -> dict[str, str]:
    """Return the top-level fields of the leading frontmatter block as strings.

    A value loses its quotes and trailing comment. A block scalar (`|`, `>-`, ...) reads as its body, so an
    empty body is an empty value. A nested mapping or list reads as an empty value. Raise `ValueError` when the
    text has no `---` delimiters or a top-level line is not a `key: value` pair.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("file needs frontmatter delimiters")
    block = lines[1:lines.index("---", 1)]
    fields: dict[str, str] = {}
    index = 0
    while index < len(block):
        line = block[index]
        index += 1
        if not line.strip() or line[0].isspace() or line[0] == "#" or line.startswith("- "):
            continue
        key, separator, value = line.partition(":")
        if not separator and not key.startswith("? "):
            raise ValueError("frontmatter line is not a key")
        key = _unquote(key.removeprefix("? "))
        value = COMMENT.sub("", value).strip()
        if BLOCK_SCALAR.fullmatch(value):
            body: list[str] = []
            while index < len(block) and (not block[index].strip() or block[index][0].isspace()):
                body.append(block[index].strip())
                index += 1
            fields[key] = "\n".join(body).strip()
        else:
            fields[key] = _unquote(line.partition(":")[2]) if separator else ""
    return fields
