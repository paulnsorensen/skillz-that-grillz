from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from skillz_experiments._cases import CodedError, digest, mapping, relative, string
from skillz_experiments.gate import Statistics

LOCATION = "evals/autoimprove.json"
GraderType = Literal["exact-json", "judge", "command", "hybrid", "audit", "journey"]
GRADERS = ("exact-json", "judge", "command", "hybrid", "audit", "journey")
JUDGED = ("audit", "judge", "hybrid", "journey")
_SKILL = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PILLAR = re.compile(r"[a-z][a-z0-9_]{0,31}")
_TOOL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
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
class Surface:
    """The extra tool surface that a contract declares for the task role.

    Each path is relative to the skill root and names a captured candidate file. `mcp_config` is a JSON file, an agent is a
    Markdown file named by its stem, and a companion is a skill directory named by its last part. Companions stay read-only.
    """

    tools: tuple[str, ...] = ()
    mcp_config: str | None = None
    agents: tuple[str, ...] = ()
    companions: tuple[str, ...] = ()

    def data(self) -> dict[str, object]:
        document: dict[str, object] = {}
        if self.tools:
            document["tools"] = list(self.tools)
        if self.mcp_config is not None:
            document["mcp_config"] = self.mcp_config
        if self.agents:
            document["agents"] = list(self.agents)
        if self.companions:
            document["companions"] = list(self.companions)
        return document


@dataclass(frozen=True)
class Contract:
    skill: str
    invocation: str
    kinds: dict[str, Grader]
    helper: Helper | None = None
    editable: tuple[str, ...] = ()
    source: str = "skill"
    surface: Surface | None = None
    statistics: Mapping[str, object] | None = None

    def in_companion(self, name: str) -> bool:
        """Return True when `name` is a companion directory or a file under one."""
        return self.surface is not None and any(name == root or name.startswith(root + "/") for root in self.surface.companions)

    def surface_fixed(self, name: str) -> bool:
        """Return True when `name` is a surface agent or the MCP config: the run never edits it."""
        return self.surface is not None and (name in self.surface.agents or name == self.surface.mcp_config)

    def read_only(self, name: str, mcp_text: str = "") -> bool:
        """Return True when the run never edits `name`.

        That covers surface agents, the MCP config, and companion files. `mcp_text` is the seed MCP config text:
        a file it names, bare or in a staged `.agents/skills/<skill>/<name>` form, runs on the host, so it is read-only too.
        """
        return self.surface_fixed(name) or self.in_companion(name) or bool(mcp_text and name in mcp_text)

    def grader(self, kind: str) -> Grader:
        if kind not in self.kinds:
            raise ValueError(f"kind {kind!r} is not declared in the contract")
        return self.kinds[kind]

    def grader_types(self) -> dict[str, str]:
        return {kind: grader.type for kind, grader in self.kinds.items()}

    def judged(self, kind: str) -> bool:
        return self.grader(kind).type in JUDGED

    def calls(self, kind: str, max_turns: int = 1) -> int:
        """Return the worst-case invocation count for one evaluation of this kind.

        A journey costs one agent call and one simulator call per turn, plus one judge call.
        """
        if self.grader(kind).type == "journey":
            return max_turns * 2 + 1
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
        if self.surface is not None:
            document["surface"] = self.surface.data()
        if self.statistics:
            document["statistics"] = dict(self.statistics)
        return document

    @property
    def identity(self) -> str:
        return digest(self.data())


HELPER_FIXTURES: tuple[dict[str, object], ...] = (
    {"input": "---\nname: contract\ndescription: Inspect\n---\n# Body\n[Guide](references/guide.md)\n",
     "returncode": 0,
     "output": {"schema_version": 4, "advisory_sentences": [], "frontmatter_keys": ["description", "name"],
                "body_line_count": 2,
                "local_link_targets": ["references/guide.md"], "long_sentences": []}},
    {"input": "---\nname: contract\n---\n[Escape](../secret)\n", "returncode": 3, "output": None,
     "error": {"error": "inspect: link escapes package", "exit_code": 3}},
)

_LEGACY_SKILLZ = Contract(
    "skillz", "$skillz audit", {"inspection": Grader("exact-json"), "audit": Grader("audit")},
    Helper("scripts/inspect-skill.pyz", "fixture.md", HELPER_FIXTURES), (), "legacy")


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
        raise ValueError(f"kind {kind} pillars must be unique lowercase names of letters, digits, and underscores; each starts with a letter and has at most 32 characters")
    return tuple(cast(list[str], names))


def _grader(value: object, kind: str) -> Grader:
    item = mapping(value)
    _fields(item, {"grader", "argv", "rubric", "capture", "pillars"}, f"kind {kind}")
    kind_type = item.get("grader")
    if kind_type not in GRADERS:
        raise ValueError(f"kind {kind} needs a grader: {', '.join(GRADERS)}")
    argv: tuple[str, ...] = ()
    if kind_type in ("command", "hybrid", "journey"):
        argv = _argv(item.get("argv"), f"kind {kind}")
    elif "argv" in item:
        raise ValueError(f"kind {kind} grader does not take argv")
    rubric = ""
    capture: tuple[str, ...] = ()
    pillars: tuple[str, ...] = ()
    if kind_type in ("judge", "hybrid", "journey"):
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
        required = {"input", "returncode", "output"}
        if not required <= set(fixture) <= required | {"error"} or type(fixture["returncode"]) is not int:
            raise ValueError("helper fixture needs input, integer returncode, and output, and may add error")
        if "error" in fixture and not isinstance(fixture["error"], dict):
            raise ValueError("helper fixture error must be an object")
        if fixture["output"] is None and fixture["returncode"] != 0 and "error" not in fixture:
            raise ValueError("helper fixture with null output and a nonzero returncode needs an error object")
        _ = string(fixture["input"], "helper fixture input")
        fixtures.append(fixture)
    return Helper(relative(string(item.get("path"), "helper path")),
                  relative(string(item.get("input", "fixture.md"), "helper input")), tuple(fixtures))


def _statistics(value: object) -> dict[str, object]:
    try:
        return Statistics.declared(value)
    except ValueError as error:
        raise CodedError("contract-statistics-invalid", f"contract {error}") from None


def _surface_error(message: str) -> CodedError:
    return CodedError("contract-surface", f"contract surface {message}")


def _read_only_reason(contract: Contract, name: str, label: str) -> str:
    if contract.surface is not None and name == contract.surface.mcp_config:
        return f"mcp_config {name} holds host-run commands, so {label} cannot hold it"
    if contract.surface_fixed(name):
        return f"agents are read-only, so {label} cannot hold {name}"
    return f"companions are read-only, so {label} cannot hold {name}"


def _surface_list(item: Mapping[str, object], field: str) -> list[object]:
    raw = item.get(field, [])
    if not isinstance(raw, list):
        raise _surface_error(f"{field} must be a list")
    return cast(list[object], raw)


def _surface_path(value: object, field: str, suffix: str = "") -> str:
    try:
        path = relative(string(value, f"surface {field} entry"))
    except ValueError:
        raise _surface_error(f"{field} entry {value!r} must be a relative, non-hidden path inside the skill") from None
    if not path.endswith(suffix):
        raise _surface_error(f"{field} entry {path!r} must end with {suffix}")
    return path


def _surface(value: object) -> Surface:
    if not isinstance(value, dict):
        raise _surface_error("must be an object")
    item = mapping(cast(object, value))
    unknown = set(item) - {"tools", "mcp_config", "agents", "companions"}
    if unknown:
        raise _surface_error(f"has unknown fields: {', '.join(sorted(unknown))}")
    tools = _surface_list(item, "tools")
    if not all(isinstance(tool, str) and _TOOL.fullmatch(tool) for tool in tools) or len(set(tools)) != len(tools):
        raise _surface_error("tools must be unique names of letters, digits, and underscores")
    config = _surface_path(item["mcp_config"], "mcp_config", ".json") if item.get("mcp_config") is not None else None
    agents = tuple(_surface_path(name, "agents", ".md") for name in _surface_list(item, "agents"))
    companions = tuple(_surface_path(name, "companions") for name in _surface_list(item, "companions"))
    if len({PurePosixPath(name).stem for name in agents}) != len(agents):
        raise _surface_error("agents must have unique file names")
    if len({PurePosixPath(name).name for name in companions}) != len(companions):
        raise _surface_error("companions must have unique directory names")
    if any(a != b and a.startswith(b + "/") for a in companions for b in companions):
        raise _surface_error("a companion must not sit inside another companion")
    surface = Surface(tuple(cast(list[str], tools)), config, agents, companions)
    if not surface.data():
        raise _surface_error("must declare tools, mcp_config, agents, or companions")
    return surface


def parse(value: object, source: str) -> Contract:
    item = mapping(value)
    if item.get("status") == "draft":
        raise CodedError("contract-unapproved", "contract has status draft; approve it before a run")
    if item.get("status") != "approved":
        raise ValueError("contract status must be approved or draft")
    _fields(item, {"schema_version", "status", "skill", "invocation", "kinds", "helper", "editable", "surface", "statistics"}, "contract")
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
    surface = _surface(item["surface"]) if "surface" in item else None
    thresholds = _statistics(item["statistics"]) if "statistics" in item else None
    contract = Contract(skill, string(item.get("invocation"), "contract invocation"),
                        {kind: _grader(entry, kind) for kind, entry in kinds.items()}, helper, editable, source, surface,
                        thresholds)
    for kind, grader in contract.kinds.items():
        if (kind == "journey") != (grader.type == "journey"):
            raise CodedError("contract-kinds", f"kind {kind} and the journey grader go together: the kind named "
                             + "journey takes the journey grader, and no other kind does")
    for label, names in ("editable", editable), ("helper", (helper.path,) if helper is not None else ()):
        for name in names:
            if contract.read_only(name):
                raise _surface_error(_read_only_reason(contract, name, label))
    return contract


def load_contract(target: Path) -> Contract:
    """Load `<target>/evals/autoimprove.json`. Fail closed when it is not a bounded regular file."""
    path = target / LOCATION
    if not os.path.lexists(path):
        raise CodedError("contract-missing", f"no autoimprove contract: create {path}")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise CodedError("contract-unreadable", f"{path} must be a bounded regular file, not a symlink or directory")
    return parse(cast(object, json.loads(path.read_text(encoding="utf-8"))), "skill")
