from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from skillz_experiments._cases import CodedError, digest, mapping, relative, string
from skillz_experiments._evaluation import HELPER_FIXTURES

LOCATION = "evals/autoimprove.json"
GraderType = Literal["exact-json", "judge", "command", "hybrid", "audit"]
GRADERS = ("exact-json", "judge", "command", "hybrid", "audit")
JUDGED = ("audit", "judge", "hybrid")
_SKILL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PILLAR = re.compile(r"[a-z][a-z0-9_]{0,31}")
PILLAR_LIMIT = 8


@dataclass(frozen=True)
class Grader:
    """One kind's grader.

    `capture` is an argv that runs after the task, in the command grader workspace, and saves files under
    `capture/` for the judge to view. `pillars` names the judge scores; the kind score is their mean.
    """

    type: GraderType
    argv: tuple[str, ...] = ()
    rubric: str = ""
    capture: tuple[str, ...] = ()
    pillars: tuple[str, ...] = ()


@dataclass(frozen=True)
class Helper:
    path: str
    input: str = "fixture.md"
    fixtures: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class Contract:
    skill: str
    invocation: str
    kinds: dict[str, Grader]
    helper: Helper | None = None
    editable: tuple[str, ...] = ()
    source: str = "skill"

    def grader(self, kind: str) -> Grader:
        if kind not in self.kinds:
            raise ValueError(f"kind {kind!r} is not declared in the contract")
        return self.kinds[kind]

    def grader_types(self) -> dict[str, str]:
        return {kind: grader.type for kind, grader in self.kinds.items()}

    def judged(self, kind: str) -> bool:
        return self.grader(kind).type in JUDGED

    def calls(self, kind: str) -> int:
        """Return the worst-case invocation count for one evaluation of this kind."""
        return 2 if self.judged(kind) else 1

    def data(self) -> dict[str, object]:
        kinds: dict[str, object] = {}
        for kind, grader in self.kinds.items():
            entry: dict[str, object] = {"grader": grader.type}
            if grader.argv:
                entry["argv"] = list(grader.argv)
            if grader.rubric:
                entry["rubric"] = grader.rubric
            if grader.capture:
                entry["capture"] = list(grader.capture)
            if grader.pillars:
                entry["pillars"] = list(grader.pillars)
            kinds[kind] = entry
        document: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": self.skill,
                                       "invocation": self.invocation, "kinds": kinds,
                                       "editable": list(self.editable)}
        if self.helper is not None:
            document["helper"] = {"path": self.helper.path, "input": self.helper.input,
                                  "fixtures": list(self.helper.fixtures)}
        return document

    @property
    def identity(self) -> str:
        return digest(self.data())


_LEGACY_SKILLZ = Contract(
    "skillz", "$skillz audit", {"inspection": Grader("exact-json"), "audit": Grader("audit")},
    Helper("scripts/inspect_skill.py", "fixture.md", HELPER_FIXTURES), (), "legacy")


def resolve(contract: Contract | None) -> Contract:
    """Return the contract, or the legacy skillz contract when a caller declares none.

    The evaluators and `load_cases` use this fallback when a caller passes no contract.
    `run` never takes it: it uses the target's contract or the intake contract.
    """
    return _LEGACY_SKILLZ if contract is None else contract


def _fields(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    if set(value) - allowed:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(set(value) - allowed))}")


def _argv(raw: object, name: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{name} needs a nonempty argv array")
    return tuple(string(part, "argv entry") for part in cast(list[object], raw))


def _pillars(raw: object, kind: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not 1 <= len(cast(list[object], raw)) <= PILLAR_LIMIT:
        raise ValueError(f"kind {kind} pillars must be a list of 1 to {PILLAR_LIMIT} names")
    names = cast(list[object], raw)
    if not all(isinstance(name, str) and _PILLAR.fullmatch(name) for name in names) or len(set(cast(list[str], names))) != len(names):
        raise ValueError(f"kind {kind} pillars must be unique lowercase names of letters, digits, and underscores")
    return tuple(cast(list[str], names))


def _grader(value: object, kind: str) -> Grader:
    item = mapping(value)
    _fields(item, {"grader", "argv", "rubric", "capture", "pillars"}, f"kind {kind}")
    kind_type = item.get("grader")
    if kind_type not in GRADERS:
        raise ValueError(f"kind {kind} needs a grader: {', '.join(GRADERS)}")
    argv: tuple[str, ...] = ()
    if kind_type in ("command", "hybrid"):
        argv = _argv(item.get("argv"), f"kind {kind}")
    elif "argv" in item:
        raise ValueError(f"kind {kind} grader does not take argv")
    rubric = ""
    capture: tuple[str, ...] = ()
    pillars: tuple[str, ...] = ()
    if kind_type in ("judge", "hybrid"):
        rubric = string(item.get("rubric"), f"kind {kind} rubric")
        capture = _argv(item["capture"], f"kind {kind} capture") if "capture" in item else ()
        pillars = _pillars(item["pillars"], kind) if "pillars" in item else ()
    else:
        for field in ("rubric", "capture", "pillars"):
            if field in item:
                raise ValueError(f"kind {kind} grader does not take {field}")
    return Grader(cast(GraderType, item["grader"]), argv, rubric, capture, pillars)


def _helper(value: object) -> Helper:
    item = mapping(value)
    _fields(item, {"path", "input", "fixtures"}, "helper")
    fixtures: list[dict[str, object]] = []
    raw = item.get("fixtures", [])
    if not isinstance(raw, list):
        raise ValueError("helper fixtures must be a list")
    for entry in cast(list[object], raw):
        fixture = mapping(entry)
        if set(fixture) != {"input", "returncode", "output"} or type(fixture["returncode"]) is not int:
            raise ValueError("helper fixture needs input, integer returncode, and output")
        _ = string(fixture["input"], "helper fixture input")
        fixtures.append(fixture)
    return Helper(relative(string(item.get("path"), "helper path")),
                  relative(string(item.get("input", "fixture.md"), "helper input")), tuple(fixtures))


def parse(value: object, source: str) -> Contract:
    item = mapping(value)
    if item.get("status") == "draft":
        raise CodedError("contract-unapproved", "contract has status draft; approve it before a run")
    if item.get("status") != "approved":
        raise ValueError("contract status must be approved or draft")
    _fields(item, {"schema_version", "status", "skill", "invocation", "kinds", "helper", "editable"}, "contract")
    if type(item.get("schema_version")) is not int or item["schema_version"] != 1:
        raise ValueError("contract needs schema_version 1")
    skill = string(item.get("skill"), "contract skill")
    if not _SKILL.fullmatch(skill):
        raise ValueError("contract skill must be a lowercase directory name")
    kinds = mapping(item.get("kinds"))
    if not kinds:
        raise ValueError("contract must declare at least one kind")
    editable_raw = item.get("editable", [])
    if not isinstance(editable_raw, list):
        raise ValueError("contract editable must be a list")
    editable = tuple(relative(string(name, "editable path")) for name in cast(list[object], editable_raw))
    helper = _helper(item["helper"]) if "helper" in item else None
    return Contract(skill, string(item.get("invocation"), "contract invocation"),
                    {kind: _grader(entry, kind) for kind, entry in kinds.items()}, helper, editable, source)


def load_contract(target: Path) -> Contract:
    """Load `<target>/evals/autoimprove.json`. Fail closed when it is not a bounded regular file."""
    path = target / LOCATION
    if not os.path.lexists(path):
        raise CodedError("contract-missing", f"no autoimprove contract: create {path}")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise CodedError("contract-unreadable", f"{path} must be a bounded regular file, not a symlink or directory")
    return parse(cast(object, json.loads(path.read_text(encoding="utf-8"))), "skill")
