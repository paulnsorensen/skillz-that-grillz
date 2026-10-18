"""Read the top-level fields of a Markdown frontmatter block."""
from __future__ import annotations

_DELIMITER = "---"


def frontmatter_end(lines: list[str]) -> int | None:
    """Return the index of the closing `---` line, or None when `lines` has no closed frontmatter block."""
    if not lines or lines[0] != _DELIMITER or _DELIMITER not in lines[1:]:
        return None
    return lines.index(_DELIMITER, 1)


def top_fields(text: str) -> dict[str, str] | None:
    """Return the top-level `key: value` pairs of the frontmatter, or None when `text` has no closed block.

    Blank and indented lines are skipped. Empty values are kept. The last duplicate key wins, as in YAML.
    """
    lines = text.splitlines()
    end = frontmatter_end(lines)
    if end is None:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line[0].isspace():
            continue
        key, _, value = line.partition(":")
        key = key.strip().removeprefix("? ").strip().strip("\"'").strip()
        fields[key] = value.strip().strip("\"'")
    return fields
