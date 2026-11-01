from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections.abc import Mapping
from typing import Literal, cast, get_args

Split = Literal["train", "validation", "holdout"]
Kind = str
Visibility = Literal["public", "private"]
LEGACY_KINDS = {"inspection": "exact-json", "audit": "audit"}


def repo_root(start: Path) -> Path | None:
    """Return the nearest ancestor of `start` (or `start`) that holds `.git`, or None."""
    here = start.resolve()
    return next((path for path in (here, *here.parents) if (path / ".git").exists()), None)


class CodedError(ValueError):
    """A coded validation failure or run stop that carries a stable machine-readable code."""

    def __init__(self, code: str, message: str, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.code: str = code
        self.details: Mapping[str, object] | None = details


def loads_untrusted(text: str) -> object:
    """Parse JSON text from a model or helper. Raise `ValueError` on bad or too deeply nested input."""
    try:
        return cast(object, json.loads(text))
    except RecursionError:
        raise ValueError("JSON is nested too deeply") from None


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


def read_text(root: Path, name: str, limit: int) -> str:
    """Return the strict UTF-8 text of a regular file of at most `limit` bytes at `name` under `root`.

    Open each path part with `O_NOFOLLOW`, so a symlink anywhere in `name` fails. Raise `ValueError` that
    names `name` and never an absolute path.
    """
    parts = PurePosixPath(relative(name)).parts
    descriptors: list[int] = []
    try:
        here = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(here)
        for part in parts[:-1]:
            here = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=here)
            descriptors.append(here)
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=here)
        with os.fdopen(leaf, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"{name} must be a regular file")
            data = stream.read(limit + 1)
    except OSError:
        raise ValueError(f"{name} cannot be read") from None
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    if len(data) > limit:
        raise ValueError(f"{name} is too large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{name} must be UTF-8 text") from None


def text_map(value: object, shared: Mapping[str, str] | None = None, *, label: str = "files") -> dict[str, str]:
    entries = mapping(dict.fromkeys(cast(list[str], value)) if isinstance(value, list) and shared is not None
                      and all(isinstance(path, str) for path in cast(list[object], value)) else cast(object, value))
    if shared is not None and (missing := [key for key, item in entries.items() if item is None and key not in shared]):
        raise ValueError(f"{label} lists {missing[0]!r}, which is not in fixtures")
    files = {relative(key): string(shared[key] if item is None and shared is not None else item, key) for key, item in entries.items()}
    forbidden = {"home", "tmp", "output", "capture", "answer.json", "response-schema.json", "AGENTS.md", "CLAUDE.md"}
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
class Journey:
    brief: str
    max_turns: int = 8
    requirements: tuple[str, ...] = ()

    def data(self) -> dict[str, object]:
        item: dict[str, object] = {"brief": self.brief, "max_turns": self.max_turns}
        if self.requirements:
            item["requirements"] = list(self.requirements)
        return item


JOURNEY_FIELDS = {"brief", "max_turns", "requirements"}
JOURNEY_EXPECTED_FIELDS = {
    "must": "each item is an observable that the transcript must show",
    "must_not": "each item is an observable that the transcript must not show",
    "questions": "the only questions that the agent may ask; an empty list means the agent asks nothing; "
                 + "when absent, any question is allowed",
    "final_stop": "the stop or state in which the conversation must end",
    "criteria": "the approved EARS statements that the judge grades, one level per criterion id",
}
CRITERIA_DRAFTING = "skills/skillz/references/criteria-drafting.md"
CRITERION_FIELDS = {"id", "text", "must", "from"}
CRITERION_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_EARS = re.compile(r"(?:(?P<trigger>WHEN|WHILE|IF|WHERE)\s+\S.*?\s+)?THE\s+\S.*?\s+SHALL\s+\S.*", re.S)
_EARS_WORDS = re.compile(r"\b(?:WHEN|WHILE|IF|WHERE|THE|SHALL)\b")
_QUOTED = re.compile(r"`[^`]*`|\"[^\"]*\"")
CRITERION_TEXT_LIMIT = 1000
# A judge level below this is an unmet criterion. Level 4 is `fully met`.
MET_LEVEL = 4
_HIDDEN = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


@dataclass(frozen=True)
class Criterion:
    """One approved EARS statement of a judged case. `source` is the JSON key `from`: the source item it converts."""

    id: str
    text: str
    must: bool = False
    source: str | None = None


def _ears(text: str) -> bool:
    """Return True for one EARS statement: at most one leading WHEN, WHILE, IF, or WHERE clause, then one THE ... SHALL ....

    A keyword inside backticks or double quotes is plain text, so a response may name `WHERE` or "THE END".
    """
    plain = _QUOTED.sub('""', text).strip()
    match = _EARS.fullmatch(plain)
    return match is not None and _EARS_WORDS.findall(plain) == ([match["trigger"]] if match["trigger"] else []) + ["THE", "SHALL"]


def _hidden(value: str) -> str | None:
    """Return the first hidden character of `value` as `U+XXXX at position N`, or None.

    A value that passes cannot add a line to a prompt or the approval question, or reorder the text that a reader sees.
    """
    for position, character in enumerate(value):
        if unicodedata.category(character) in _HIDDEN:
            return f"U+{ord(character):04X} at position {position}"
    return None


def case_criteria(value: object, case_id: object) -> tuple[Criterion, ...]:
    """Return the `expected.criteria` of a judged case. Raise `case-criteria-missing` or `case-criteria-invalid`."""
    raw = cast(dict[object, object], value).get("criteria") if isinstance(value, dict) else None
    if raw is None or raw == []:
        raise CodedError("case-criteria-missing", f"judged case {case_id!r} has no expected.criteria; draft them with "
                         + f"the criteria drafting procedure in {CRITERIA_DRAFTING}")
    code = "case-criteria-invalid"
    if not isinstance(raw, list):
        raise CodedError(code, f"judged case {case_id!r}: expected.criteria must be a list of criteria")
    found: list[Criterion] = []
    for position, entry in enumerate(cast(list[object], raw)):
        item = cast(dict[object, object], entry) if isinstance(entry, dict) else {}
        name = item.get("id")
        where = f"judged case {case_id!r} criterion {name if isinstance(name, str) else '#' + str(position)}"
        if not item or set(item) - CRITERION_FIELDS or not {"id", "text"} <= set(item):
            raise CodedError(code, f"{where}: a criterion is an object with id and text, and optional must and from")
        if not isinstance(name, str) or not CRITERION_ID.fullmatch(name):
            raise CodedError(code, f"{where}: id must be 1 to 64 letters, digits, dots, dashes, or underscores")
        if any(criterion.id == name for criterion in found):
            raise CodedError(code, f"{where}: the id repeats")
        text = item["text"]
        if isinstance(text, str) and len(text) > CRITERION_TEXT_LIMIT:
            raise CodedError(code, f"{where}: text must be at most {CRITERION_TEXT_LIMIT} characters")
        if isinstance(text, str) and (hidden := _hidden(text)):
            raise CodedError(code, f"{where}: text must be one EARS statement on one line, and holds a control, format, "
                             + f"surrogate, or line-separator character: {hidden}")
        if not isinstance(text, str) or not _ears(text):
            raise CodedError(code, f"{where}: text must be one EARS statement on one line: at most one leading WHEN, WHILE, "
                             + "IF, or WHERE clause, then one THE <subject> SHALL <response>; quote a keyword inside the "
                             + "response with backticks")
        must = item.get("must", False)
        source = item.get("from")
        if type(must) is not bool:
            raise CodedError(code, f"{where}: must must be true or false")
        if isinstance(source, str) and (hidden := _hidden(source)):
            raise CodedError(code, f"{where}: from must be one line of text, and holds a control, format, surrogate, "
                             + f"or line-separator character: {hidden}")
        if source is not None and (not isinstance(source, str) or not source.strip() or len(source) > CRITERION_TEXT_LIMIT):
            raise CodedError(code, f"{where}: from must be one line of text, at most {CRITERION_TEXT_LIMIT} characters, "
                             + "that names a source item")
        found.append(Criterion(name, text, must, source))
    return tuple(found)


def _entries(value: object, label: str, code: str, case_id: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CodedError(code, f"journey case {case_id!r}: expected.{label} must be a list of text")
    names: list[str] = []
    for entry in cast(list[object], value):
        if not isinstance(entry, str) or not entry.strip():
            raise CodedError(code, f"journey case {case_id!r}: expected.{label} entries must be nonempty text")
        if entry in names:
            raise CodedError(code, f"journey case {case_id!r}: expected.{label} repeats {entry!r}")
        names.append(entry)
    return tuple(names)


def journey_expected(value: object, case_id: object) -> None:
    """Validate the closed `expected` object of a journey case. Raise `CodedError` on a violation."""
    if value is None:
        raise CodedError("expected-missing", f"journey case {case_id!r} needs an expected object")
    code = "journey-expected-invalid"
    where = f"journey case {case_id!r}"
    advice = "fix the case, or start a new run when it comes from an older run"
    if not isinstance(value, dict):
        raise CodedError(code, f"{where}: expected must be an object; {advice}")
    expected = cast(dict[object, object], value)
    unknown = sorted(str(key) for key in expected if key not in JOURNEY_EXPECTED_FIELDS)
    if unknown:
        raise CodedError(code, f"{where}: expected has unknown key {', '.join(unknown)}; allowed keys: "
                         + f"{', '.join(JOURNEY_EXPECTED_FIELDS)}; {advice}")
    lists = {name: _entries(expected[name], name, code, case_id) for name in ("must", "must_not", "questions")
             if name in expected}
    if not lists.get("must") and not lists.get("must_not"):
        raise CodedError(code, f"{where}: expected needs a nonempty must or must_not; {advice}")
    if "final_stop" in expected and (not isinstance(expected["final_stop"], str) or not expected["final_stop"].strip()):
        raise CodedError(code, f"{where}: expected.final_stop must be nonempty text; {advice}")
    if "criteria" in expected:  # a seed or an older frozen run may leave them out; intake requires them on a judged case
        _ = case_criteria(expected, case_id)


def journey_of(item: dict[str, object], journey: bool) -> Journey | None:
    """Return the journey fields of a case of a journey-graded kind, else None. Journey fields elsewhere are an error."""
    if not journey:
        if JOURNEY_FIELDS & set(item):
            raise ValueError(f"{', '.join(sorted(JOURNEY_FIELDS & set(item)))} only belong to a journey case")
        return None
    brief = item.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise CodedError("brief-missing", f"journey case {item.get('id')!r} needs a nonempty brief")
    turns = item.get("max_turns", 8)
    if type(turns) is not int or turns < 1:
        raise ValueError("max_turns must be a positive integer")
    journey_expected(item.get("expected"), item.get("id"))
    requirements = item.get("requirements", [])
    if not isinstance(requirements, list):
        raise ValueError("requirements must be a list of text")
    names = [string(entry, "requirement") for entry in cast(list[object], requirements)]
    if len(set(names)) != len(names):
        raise ValueError("requirements must be unique")
    return Journey(brief, turns, tuple(names))


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
    journey: Journey | None = None

    @property
    def eligible(self) -> bool:
        return bool(self.request and (self.files or self.journey is not None) and self.expected is not None and self.provider_approved
                    and (not isinstance(self.expected, Audit) or self.labels_reviewed))


def _kind(item: dict[str, object], kinds: Mapping[str, str]) -> str:
    if "kind" not in item and "inspection" not in kinds and len(kinds) != 1:
        raise ValueError("case needs a kind declared in the contract")
    kind = item["kind"] if "kind" in item else "inspection" if "inspection" in kinds else next(iter(kinds))
    if not isinstance(kind, str) or kind not in kinds:
        raise ValueError(f"kind {kind!r} is not declared in the contract; declared kinds: {', '.join(sorted(kinds))}")
    return kind


def _case(value: object, kinds: Mapping[str, str], fixtures: Mapping[str, str]) -> Case:
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
    kind = _kind(item, kinds)
    reviewed = item.get("labels_reviewed", False)
    if type(reviewed) is not bool:
        raise ValueError("labels_reviewed must be boolean")
    files = text_map(item.get("files", {}), fixtures, label=f"case {item.get('id')!r} files")
    expected = audit_labels(item.get("expected"), files) if kinds[kind] == "audit" else item.get("expected")
    if expected is None and kinds[kind] == "command":
        raise CodedError("expected-missing", f"case {item.get('id')!r} of kind {kind!r} needs an expected value")
    return Case(
        string(item.get("id"), "id"), string(item.get("family"), "family"), cast(Split, split),
        request, files, expected,
        string(item.get("provenance"), "provenance"), item.get("provider_approved") is True,
        cast(Visibility, visibility), kind, reviewed, journey_of(item, kinds[kind] == "journey"),
    )


def load_manifest(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError("manifest must be a bounded regular file, not a symlink")
    document = mapping(cast(object, json.loads(path.read_text(encoding="utf-8"))))
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1 or not isinstance(document.get("cases"), list):
        raise ValueError("expected schema_version 1 and cases list")
    return document


def load_cases(path: Path, kinds: Mapping[str, str] | None = None) -> list[Case]:
    """Load cases. `kinds` maps each declared kind to its grader type."""
    document = load_manifest(path)
    declared, fixtures = LEGACY_KINDS if kinds is None else kinds, text_map(document.get("fixtures", {}))
    cases = [_case(item, declared, fixtures) for item in cast(list[object], document["cases"])]
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
