from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast, get_args

Split = Literal["train", "validation", "holdout"]
Kind = Literal["inspection", "audit"]
Visibility = Literal["public", "private"]

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
        raise ValueError("path must not be hidden")
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
class Citation:
    path: str
    start: int
    end: int
    quote: str


@dataclass(frozen=True)
class Label:
    id: str
    severity: str
    explanation: str
    evidence: tuple[Citation, ...]


@dataclass(frozen=True)
class Audit:
    labels: tuple[Label, ...]


def citation(value: object, files: dict[str, str]) -> Citation:
    item = mapping(value)
    if set(item) != {"path", "start", "end", "quote"}:
        raise ValueError("citation requires path, start, end, and quote")
    path = relative(string(item["path"], "citation path"))
    start, end = item["start"], item["end"]
    if type(start) is not int or type(end) is not int or path not in files:
        raise ValueError("citation requires integer lines and a fixture path")
    lines = files[path].splitlines()
    if not 1 <= start <= end <= len(lines) or item["quote"] != "\n".join(lines[start - 1:end]):
        raise ValueError("citation must quote the exact inclusive fixture lines")
    return Citation(path, start, end, cast(str, item["quote"]))


def severity(value: object) -> str:
    if value not in ("critical", "high", "medium", "low"):
        raise ValueError("severity must be critical, high, medium, or low")
    return cast(str, value)


def audit_labels(value: object, files: dict[str, str]) -> Audit:
    expected = mapping(value)
    if set(expected) != {"labels"} or not isinstance(expected["labels"], list):
        raise ValueError("audit expected requires a labels list")
    labels: list[Label] = []
    for raw in cast(list[object], expected["labels"]):
        item = mapping(raw)
        if set(item) != {"id", "severity", "explanation", "evidence"}:
            raise ValueError("label requires id, severity, explanation, and evidence")
        evidence = item["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("label requires nonempty evidence")
        label = Label(string(item["id"], "label id"), severity(item["severity"]),
                      string(item["explanation"], "label explanation"),
                      tuple(citation(entry, files) for entry in cast(list[object], evidence)))
        if any(existing.id == label.id for existing in labels):
            raise ValueError("duplicate label id")
        labels.append(label)
    return Audit(tuple(labels))


@dataclass(frozen=True)
class Case:
    identifier: str
    family: str
    split: Split
    request: str
    files: dict[str, str]
    expected: object
    provenance: str
    provider_approved: bool
    visibility: Visibility = "private"
    kind: Kind = "inspection"
    labels_reviewed: bool = False

    @property
    def eligible(self) -> bool:
        return bool(self.request and self.files and self.expected is not None and self.provider_approved
                    and (self.kind != "audit" or self.labels_reviewed))


def _case(value: object) -> Case:
    item = mapping(value)
    split = string(item.get("split"), "split")
    if split not in get_args(Split):
        raise ValueError("split must be train, validation, or holdout")
    request = item.get("request", "")
    if not isinstance(request, str):
        raise ValueError("request must be text")
    visibility = item.get("visibility", "private")
    if visibility not in get_args(Visibility):
        raise ValueError("visibility must be public or private")
    kind = item.get("kind", "inspection")
    if kind not in get_args(Kind):
        raise ValueError("kind must be inspection or audit")
    reviewed = item.get("labels_reviewed", False)
    if type(reviewed) is not bool:
        raise ValueError("labels_reviewed must be boolean")
    files = text_map(item.get("files", {}))
    expected = audit_labels(item.get("expected"), files) if kind == "audit" else item.get("expected")
    return Case(
        string(item.get("id"), "id"), string(item.get("family"), "family"), cast(Split, split),
        request, files, expected,
        string(item.get("provenance"), "provenance"), item.get("provider_approved") is True,
        cast(Visibility, visibility), cast(Kind, kind), reviewed,
    )


def load_cases(path: Path) -> list[Case]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
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
