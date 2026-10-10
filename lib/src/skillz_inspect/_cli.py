from __future__ import annotations

from pathlib import Path

import fromargs

from skillz_inspect._inspect import inspect

app = fromargs.App("inspect-skill", help="Report package facts of a skill file without grading it.")


@app.default
def inspect_command(path: Path) -> dict[str, object]:
    """Report frontmatter keys, body size, local links, and long prose sentences of a Markdown file."""
    try:
        return inspect(path)
    except (OSError, UnicodeError, ValueError) as error:
        raise fromargs.contract_error(error, context="inspect") from error


def main(argv: list[str] | None = None) -> int:
    return app.run(argv)
