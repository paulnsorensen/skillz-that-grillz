"""Check that a change.json file holds the keys of one change with valid values."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from fromargs import App, CliError

TYPES = ("feat", "fix", "perf", "docs", "refactor")

app = App(name="change-check")


@app.default
def check(path: str) -> dict[str, bool]:
    """Print {"ok": true} when PATH holds a valid change object."""
    try:
        loaded = cast(object, json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise CliError(f"cannot read {path}: {error}", exit_code=3) from error
    if not isinstance(loaded, dict):
        raise CliError("change.json is not a JSON object", exit_code=3)
    change = cast(dict[str, object], loaded)
    ticket, scope, summary = change.get("ticket"), change.get("scope"), change.get("summary")
    if ticket is not None and not (isinstance(ticket, str) and re.fullmatch(r"[A-Z]{2,5}-[0-9]{1,5}", ticket)):
        raise CliError("ticket must be null or an id such as ABC-123", exit_code=3)
    if change.get("type") not in TYPES:
        raise CliError(f"type must be one of {', '.join(TYPES)}", exit_code=3)
    if scope is not None and not (isinstance(scope, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,19}", scope)):
        raise CliError("scope must be null or one lowercase word", exit_code=3)
    if not isinstance(summary, str) or not summary.strip():
        raise CliError("summary must be one nonblank line", exit_code=3)
    if not isinstance(change.get("breaking"), bool):
        raise CliError("breaking must be true or false", exit_code=3)
    return {"ok": True}


def main() -> None:
    app.main()
