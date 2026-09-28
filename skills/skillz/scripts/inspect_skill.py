#!/usr/bin/env python3
"""Report package facts without grading the skill."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def inspect(path: Path) -> dict[str, object]:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("symlinks are forbidden")
    if not path.is_file() or path.stat().st_size > 262144:
        raise ValueError("input must be a regular file of at most 262144 bytes")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("frontmatter delimiters are required")
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
        if any(parent.is_symlink() for parent in (destination, *destination.parents)):
            raise ValueError("symlinks are forbidden")
        links.add(target)
    return {"schema_version": 1, "frontmatter_keys": keys,
            "body_line_count": len(lines[end + 1:]), "local_link_targets": sorted(links)}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1:
            raise ValueError("usage: inspect_skill.py PATH")
        result = inspect(Path(args[0]))
    except (OSError, UnicodeError, ValueError) as error:
        print(json.dumps({"schema_version": 1, "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
