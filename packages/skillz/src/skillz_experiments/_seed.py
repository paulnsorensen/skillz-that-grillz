"""Seed the case draft from the skill's own `evals/evals.json`.

The runner never writes the draft. It maps each eval entry to a seed case and shows the map in the
`cases-missing` stop. The coverage check stops the run when the draft drops or changes an entry.
To drop an entry, remove it from `evals.json`.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError, loads_untrusted, mapping, read_text, relative, string, text_map
from skillz_experiments._intake import DRAFT_LIMIT, DraftCase

EVALS = "evals/evals.json"
FIXTURES = "evals/files/"
SEED_LIMIT = DRAFT_LIMIT // 2
READ_LIMIT = SEED_LIMIT


@dataclass(frozen=True)
class SeedHeader:
    """The fields of a seed case that the coverage check compares."""

    identifier: str
    request: str
    kind: str
    family: str | None

    def case(self) -> dict[str, object]:
        case: dict[str, object] = {"id": self.identifier, "request": self.request, "kind": self.kind, "source": "skill"}
        if self.family is not None:
            case["family"] = self.family
        elif self.kind != "task":
            case["family"] = self.identifier
        return case


Entry = tuple[SeedHeader, dict[str, object]]


def _strings(item: dict[str, object], key: str) -> list[str]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in cast(list[object], value)):
        raise ValueError(f"{key} must be a list of text")
    return cast(list[str], value)


def _entry(value: object, position: int) -> Entry:
    """Validate one eval entry. Return its seed header and the entry. No file is read."""
    label = f"entry #{position}"
    try:
        item = mapping(value)
        name = string(item.get("name"), "name")
        label = f"entry {name}"
        prompt = string(item.get("prompt"), "prompt")
        if not isinstance(item.get("expected_output", ""), str):
            raise ValueError("expected_output must be text")
        assertions = _strings(item, "assertions")
        if not all(assertion.strip() for assertion in assertions):
            raise ValueError("assertions must hold nonempty text")
        _ = _strings(item, "files")
        skill = item.get("expected_skill")
        if "expected_skill" in item and skill is not None and (not isinstance(skill, str) or not skill.strip()):
            raise ValueError("expected_skill must be nonempty text or null")
        kind = "near-miss" if "expected_skill" in item and skill is None else "task" if assertions else "trigger"
        family = string(item["family"], "family") if "family" in item else None
        return SeedHeader(name, prompt, kind, family), item
    except ValueError as error:
        raise ValueError(f"{label}: {error}") from None


def _entries(target: Path) -> list[Entry] | None:
    """Return the validated entries in file order, or None when the skill has no `evals.json`."""
    if not (target / EVALS).is_symlink() and not (target / EVALS).exists():
        return None
    try:
        entries = mapping(loads_untrusted(read_text(target, EVALS, READ_LIMIT))).get("evals")
        if not isinstance(entries, list):
            raise ValueError("evals must be a list")
        parsed = [_entry(entry, position) for position, entry in enumerate(cast(list[object], entries), 1)]
        names = [header.identifier for header, _item in parsed]
        if len(set(names)) != len(names):
            raise ValueError("entry names must be unique")
    except ValueError as error:
        raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: {error}") from None
    return parsed


def _fixture(target: Path, path: str, cache: dict[str, str]) -> str:
    """Return the text of a fixture under `evals/files/`. Read each distinct path once."""
    if path not in cache:
        if not relative(path).startswith(FIXTURES):
            raise ValueError(f"{path} must be under {FIXTURES}")
        cache[path] = read_text(target, path, READ_LIMIT)
        if sum(len(text.encode("utf-8")) for text in cache.values()) > SEED_LIMIT:
            raise ValueError(f"the seed is too large; its fixtures pass {SEED_LIMIT} bytes")
    return cache[path]


def seed_cases(target: Path) -> list[dict[str, object]] | None:
    """Return one seed case per eval entry in file order, or None when the skill has no `evals.json`.

    A task case also holds its fixtures and reference. Raise `CodedError` `evals-unreadable` when the
    file or a fixture it lists is unsafe or malformed, or when the seed passes `SEED_LIMIT` bytes of JSON.
    """
    entries = _entries(target)
    if entries is None:
        return None
    cases: list[dict[str, object]] = []
    cache: dict[str, str] = {}
    total = 0
    for header, item in entries:
        case = header.case()
        if header.kind == "task":
            try:
                case["files"] = text_map({path: _fixture(target, path, cache) for path in _strings(item, "files")})
            except ValueError as error:
                raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: entry {header.identifier}: {error}") from None
            case["expected"] = {"expected_output": item.get("expected_output", ""), "assertions": item["assertions"]}
        total += len(json.dumps(case))
        if total > SEED_LIMIT:
            raise CodedError("evals-unreadable", f"{EVALS} cannot seed the cases: the seed is too large; it passes "
                             + f"{SEED_LIMIT} bytes at entry {header.identifier}")
        cases.append(case)
    return cases


def check_coverage(target: Path, draft: Sequence[DraftCase]) -> None:
    """Raise `cases-uncovered` unless each eval entry is in the draft with its name, kind, and request.

    This reads `evals.json` only. It does not read any fixture.
    """
    drafted = {case.identifier: case for case in draft}
    problems: list[str] = []
    for header, _item in _entries(target) or []:
        case = drafted.get(header.identifier)
        if case is None:
            problems.append(f"- {header.identifier}: missing from the draft")
            continue
        changed = [f"kind is {case.kind!r}, not {header.kind!r}"] if case.kind != header.kind else []
        if case.request != header.request:
            changed.append("request changed")
        if changed:
            problems.append(f"- {header.identifier}: {'; '.join(changed)}")
    if problems:
        raise CodedError("cases-uncovered", "the draft drops or changes entries of " + EVALS + ":\n"
                         + "\n".join(problems) + "\nrestore each entry in the draft, or remove it from " + EVALS
                         + " to drop it on purpose, then call run again")
