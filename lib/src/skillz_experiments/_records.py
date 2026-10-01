from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import cast

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import digest, load_cases, mapping


def write(path: Path, value: object) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            _ = stream.write("\n")
        _ = temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read(path: Path) -> dict[str, object]:
    if path.is_symlink() or path.stat().st_size > 10_000_000:
        raise ValueError("unsafe run record")
    return mapping(cast(object, json.loads(path.read_text(encoding="utf-8"))))


def prepare(manifest: Path, target: Path, out: Path, components: list[str] | None = None) -> dict[str, object]:
    cases = load_cases(manifest)
    editable = ["SKILL.md"]
    if (target / "scripts/inspect_skill.py").is_file():
        editable.append("scripts/inspect_skill.py")
    for name in components or []:
        if not (name.startswith("references/") and name.endswith(".md")):
            raise ValueError("extra editable components must be Markdown references")
        if name not in editable:
            editable.append(name)
    seed = Candidate.capture(target, editable)
    out.mkdir(mode=0o700)
    document = {"schema_version": 1, "cases": [dict(asdict(case), id=case.identifier) for case in cases]}
    record: dict[str, object] = {
        "schema_version": 1, "phase": "prepared", "calls": 0,
        "target_root": str(target.resolve()),
        "dataset_hash": digest(document), "seed_hash": seed.identity,
        "seed": seed.files, "editable": list(seed.editable), "arms": {}, "outcomes": [],
        "visibility": "public" if all(case.visibility == "public" for case in cases) else "private",
    }
    write(out / "cases.json", document)
    write(out / "run.json", record)
    return {"run": str(out), "eligible": sum(case.eligible for case in cases),
            "diagnostic": sum(not case.eligible for case in cases), "live_calls": 0}
