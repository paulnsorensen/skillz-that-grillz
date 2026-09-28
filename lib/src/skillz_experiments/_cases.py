from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in cast(dict[object, object], value)):
        raise ValueError("expected a JSON object")
    return cast(dict[str, object], value)


def string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in value or "\x00" in value or path.as_posix() != value:
        raise ValueError("unsafe relative path")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("hidden paths are forbidden")
    return value


def text_map(value: object) -> dict[str, str]:
    files = {relative(key): string(item, key) for key, item in mapping(value).items()}
    forbidden = {"home", "tmp", "answer.json", "response-schema.json", "AGENTS.md", "CLAUDE.md"}
    if any(PurePosixPath(key).parts[0] in forbidden or PurePosixPath(key).name == "AGENTS.md" for key in files):
        raise ValueError("fixture collides with runtime-owned paths")
    return files


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Case:
    identifier: str
    family: str
    split: str
    request: str
    files: dict[str, str]
    expected: object
    provenance: str
    provider_approved: bool
    visibility: str = "private"

    @property
    def eligible(self) -> bool:
        return bool(self.request and self.files and self.expected is not None and self.provider_approved)


def _case(value: object) -> Case:
    item = mapping(value)
    split = string(item.get("split"), "split")
    if split not in {"train", "validation", "holdout"}:
        raise ValueError("split must be train, validation, or holdout")
    request = item.get("request", "")
    if not isinstance(request, str):
        raise ValueError("request must be text")
    visibility = item.get("visibility", "private")
    if visibility not in {"public", "private"}:
        raise ValueError("visibility must be public or private")
    return Case(
        string(item.get("id"), "id"), string(item.get("family"), "family"), split,
        request, text_map(item.get("files", {})), item.get("expected"),
        string(item.get("provenance"), "provenance"), item.get("provider_approved") is True,
        cast(str, visibility),
    )


def load_cases(path: Path) -> list[Case]:
    if not path.is_file() or any(part.is_symlink() for part in (path, *path.parents)) or path.stat().st_size > 2_000_000:
        raise ValueError("manifest must be a bounded regular file, not a symlink")
    document = mapping(cast(object, json.loads(path.read_text(encoding="utf-8"))))
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("expected schema_version 1 and cases list")
    cases = [_case(item) for item in cast(list[object], document["cases"])]
    identifiers: set[str] = set()
    families: dict[str, str] = {}
    for case in cases:
        if case.identifier in identifiers:
            raise ValueError("duplicate case identifier")
        if case.family in families and families[case.family] != case.split:
            raise ValueError("task family crosses dataset splits")
        identifiers.add(case.identifier)
        families[case.family] = case.split
    return cases
