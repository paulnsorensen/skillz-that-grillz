"""Read a RUN/cases.seed.json file back as cases with inline files, as the agent sees them (gh-580)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast


def read_seed(path: Path) -> list[dict[str, object]]:
    """Return the seed cases, each `files` list resolved against the shared `fixtures` map of the file."""
    document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    fixtures = cast(dict[str, str], document["fixtures"])
    return [case | {"files": {name: fixtures[name] for name in cast(list[str], case["files"])}} if isinstance(case.get("files"), list) else case
            for case in cast(list[dict[str, object]], document["cases"])]
