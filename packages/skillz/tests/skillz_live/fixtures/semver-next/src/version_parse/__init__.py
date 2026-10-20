"""Split a semantic version into its parts. The one wedge target of the `semver-next` fixture skill."""
from __future__ import annotations

import re

from fromargs import App, CliError

VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.(0|[1-9]\d*))?")

app = App(name="version-parse")


@app.default
def parse(version: str) -> dict[str, int | None]:
    """Print the major, minor, patch, and rc parts of VERSION as JSON."""
    found = VERSION.fullmatch(version.strip())
    if found is None:
        raise CliError(f"not a semantic version: {version!r}", exit_code=3)
    major, minor, patch, rc = found.groups()
    return {"major": int(major), "minor": int(minor), "patch": int(patch), "rc": None if rc is None else int(rc)}


def main() -> None:
    app.main()
