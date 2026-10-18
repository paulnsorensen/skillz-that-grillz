"""The one reader of `evals/evals.json`, shared by the seed and the repository validator.

The strict profile checks the files that this repository ships. The lenient profile reads files from
outside the repository (agentskills.io, Anthropic skill-creator, NVIDIA SkillEvaluator). Both profiles
resolve the case kind and the case id with the same code. This module uses the standard library only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from skillz_experiments._cases import JOURNEY_FIELDS, CodedError, journey_of, string, text_map

EVAL_KINDS = ("trigger", "near-miss", "task")
SOURCES = ("skill", "session")
FIXTURES = "evals/files/"
_TEXT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


@dataclass(frozen=True)
class EvalEntry:
    """One valid eval entry. `inferred` is true when the entry has no `kind` and the reader chose it."""

    index: int
    identifier: str
    prompt: str
    kind: str
    inferred: bool
    family: str | None
    files: tuple[str, ...]
    expected_output: str
    assertions: tuple[str, ...]


@dataclass(frozen=True)
class ParsedEvals:
    entries: tuple[EvalEntry, ...]
    errors: tuple[str, ...]


def _text(value: object, name: str, problems: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{name} must be nonempty text")
        return None
    return value


def _texts(item: dict[str, object], key: str, problems: list[str]) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in cast(list[object], value)):
        problems.append(f"{key} must be a list of text")
        return ()
    values = tuple(cast(list[str], value))
    if key == "assertions" and not all(entry.strip() for entry in values):
        problems.append("assertions must hold nonempty text")
    return values


def _identity(item: dict[str, object], strict: bool, problems: list[str]) -> tuple[str | None, object]:
    """Return the entry `name` and `id` when valid, and note each problem."""
    name: str | None = None
    if "name" in item:
        name = _text(item["name"], "name", problems)
    elif strict:
        problems.append("missing required field 'name'")
    ident = item.get("id")
    if "id" in item:
        text_ok = not strict and isinstance(ident, str) and _TEXT_ID.fullmatch(ident) is not None
        if isinstance(ident, bool) or not (isinstance(ident, int) or text_ok):
            problems.append("id must be an int" if strict
                            else "id must be an int or short text of letters, digits, '.', '_', '-'")
            ident = None
    elif strict:
        problems.append("missing required field 'id'")
    elif "name" not in item:
        problems.append("needs a name or an id")
    return name, ident


def _kind(item: dict[str, object], strict: bool, assertions: tuple[str, ...], expected_output: str,
          problems: list[str]) -> tuple[str | None, bool]:
    """Resolve the kind. Return it, and whether the reader inferred it."""
    skill = item.get("expected_skill")
    if "expected_skill" in item and skill is not None and (not isinstance(skill, str) or not skill.strip()):
        problems.append("expected_skill must be nonempty text or null")
        return None, False
    null_skill = "expected_skill" in item and skill is None
    if "kind" in item:
        kind = item["kind"]
        if kind not in EVAL_KINDS:
            problems.append(f"kind must be one of {', '.join(EVAL_KINDS)}")
            return None, False
        if null_skill and kind != "near-miss":
            problems.append(f"expected_skill is null, so kind must be near-miss, not {kind}")
        elif assertions and kind != "task":
            problems.append(f"a {kind} entry must have no assertions")
        elif kind == "task" and not assertions and not expected_output.strip():
            problems.append("a task entry needs expected_output or assertions")
        return cast(str, kind), False
    if strict:
        problems.append("missing required field 'kind'")
        return None, False
    if null_skill:
        if assertions:
            problems.append("a near-miss entry (expected_skill is null) must have no assertions")
        return "near-miss", True
    if assertions:
        return "task", True
    if skill is not None:
        problems.append("kind is missing and expected_skill names a skill with no assertions; set kind to trigger or task")
        return None, False
    if not expected_output.strip():
        problems.append("kind is missing and the entry has no assertions or expected_output; set kind")
        return None, False
    return "task", True


def _fixture_problem(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (not path.startswith(FIXTURES) or "\\" in path or "\x00" in path or ".." in parts
            or PurePosixPath(path).as_posix() != path or any(part.startswith(".") for part in parts))


def _entry(item: dict[str, object], index: int, strict: bool, problems: list[str]) -> EvalEntry | None:
    name, ident = _identity(item, strict, problems)
    prompt = _text(item.get("prompt"), "prompt", problems)
    expected = item.get("expected_output", "")
    if strict and "expected_output" not in item:
        problems.append("missing required field 'expected_output'")
    elif strict and (not isinstance(expected, str) or not expected.strip()):
        problems.append("expected_output must be nonempty text")
    elif not isinstance(expected, str):
        problems.append("expected_output must be text")
    expected_output = expected if isinstance(expected, str) else ""
    assertions = _texts(item, "assertions", problems)
    if strict and "files" not in item:
        problems.append("missing required field 'files'")
    files = _texts(item, "files", problems)
    if strict:
        problems.extend(f"files: {path!r} must be a plain path under {FIXTURES}" for path in files
                        if _fixture_problem(path))
    family = _text(item["family"], "family", problems) if "family" in item else None
    kind, inferred = _kind(item, strict, assertions, expected_output, problems)
    identifier = name if name is not None else f"eval-{ident}" if ident is not None else None
    if problems or prompt is None or kind is None or identifier is None:
        return None
    return EvalEntry(index, identifier, prompt, kind, inferred, family, files, expected_output, assertions)


def parse_evals(document: object, *, strict: bool) -> ParsedEvals:
    """Check an `evals.json` document. Return the valid entries and every error, each with its `evals[i]` location."""
    if not isinstance(document, dict) or not all(isinstance(key, str) for key in cast(dict[object, object], document)):
        return ParsedEvals((), ("expected a JSON object with 'evals'",))
    record = cast(dict[str, object], document)
    errors: list[str] = []
    if strict:
        skill_name = record.get("skill_name")
        if not isinstance(skill_name, str) or not skill_name.strip():
            errors.append("'skill_name' must be a non-empty string")
    evals = record.get("evals")
    if not isinstance(evals, list):
        return ParsedEvals((), (*errors, "evals must be a list"))
    if strict and not evals:
        return ParsedEvals((), (*errors, "'evals' must contain at least one entry"))
    entries: list[EvalEntry] = []
    names: set[str] = set()
    ids: set[object] = set()
    resolved: set[str] = set()
    for index, raw in enumerate(cast(list[object], evals)):
        if not isinstance(raw, dict) or not all(isinstance(key, str) for key in cast(dict[object, object], raw)):
            errors.append(f"evals[{index}]: expected a JSON object")
            continue
        item = cast(dict[str, object], raw)
        problems: list[str] = []
        entry = _entry(item, index, strict, problems)
        name, ident = item.get("name"), item.get("id")
        has_id = isinstance(ident, (int, str)) and not isinstance(ident, bool)
        if isinstance(name, str) and name in names:
            problems.append(f"duplicate name {name!r}")
        elif has_id and ident in ids:
            problems.append(f"duplicate id {ident!r}")
        elif entry is not None and entry.identifier in resolved:
            problems.append(f"duplicate case id {entry.identifier!r}")
        if isinstance(name, str):
            names.add(name)
        if has_id:
            ids.add(ident)
        if entry is not None:
            resolved.add(entry.identifier)
        errors.extend(f"evals[{index}]: {problem}" for problem in problems)
        if entry is not None and not problems:
            entries.append(entry)
    return ParsedEvals(tuple(entries), tuple(errors))


JOURNEY_KEYS = {"id", "family", "kind", "request", "files", "expected", "source"} | JOURNEY_FIELDS
JOURNEY_REQUIRED = ("id", "family", "kind", "request", "brief", "expected", "source")


@dataclass(frozen=True)
class ParsedJourneys:
    """The valid journey cases of an `evals/journeys.json` document, in draft-case shape, and every error."""

    cases: tuple[dict[str, object], ...]
    errors: tuple[str, ...]


def _journey(item: dict[str, object], strict: bool) -> dict[str, object]:
    """Return the seed case of one journey item. Raise `ValueError` for the first problem."""
    unknown = sorted(set(item) - JOURNEY_KEYS)
    if unknown:
        raise ValueError(f"unknown fields {', '.join(unknown)}")
    required = JOURNEY_REQUIRED if strict else ("id", "kind", "request", "brief", "expected")
    missing = [name for name in required if name not in item]
    if missing:
        raise ValueError(f"missing required field {', '.join(repr(name) for name in missing)}")
    ident = string(item["id"], "id")
    kind = string(item["kind"], "kind")
    if kind in EVAL_KINDS:
        raise ValueError(f"kind must name a journey kind, not {kind}")
    source = item.get("source", "skill")
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    case: dict[str, object] = {"id": ident, "family": string(item.get("family", ident), "family"), "kind": kind,
                               "request": string(item["request"], "request"), "source": source}
    journey = journey_of(item, True)
    if journey is None:
        raise ValueError("a journey case needs journey fields")
    case |= journey.data()
    if "files" in item:
        case["files"] = text_map(item["files"])
    case["expected"] = item["expected"]
    return case


def parse_journeys(document: object, *, strict: bool) -> ParsedJourneys:
    """Check an `evals/journeys.json` document: a list of journey cases in the draft-case shape.

    Return the valid cases and every error, each with its `journeys[i]` location. A case `kind` is any
    nonempty name other than the eval kinds; the contract checks it at intake.
    """
    if not isinstance(document, list):
        return ParsedJourneys((), ("expected a JSON list of journey cases",))
    items = cast(list[object], document)
    if strict and not items:
        return ParsedJourneys((), ("the list must contain at least one journey case",))
    cases: list[dict[str, object]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, dict) or not all(isinstance(key, str) for key in cast(dict[object, object], raw)):
            errors.append(f"journeys[{index}]: expected a JSON object")
            continue
        try:
            case = _journey(cast(dict[str, object], raw), strict)
        except (ValueError, CodedError) as error:
            errors.append(f"journeys[{index}]: {error}")
            continue
        if case["id"] in seen:
            errors.append(f"journeys[{index}]: duplicate case id {case['id']!r}")
            continue
        seen.add(cast(str, case["id"]))
        cases.append(case)
    return ParsedJourneys(tuple(cases), tuple(errors))
