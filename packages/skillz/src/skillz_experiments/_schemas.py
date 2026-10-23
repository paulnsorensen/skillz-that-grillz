"""JSON Schemas (draft 2020-12) for the author files, generated from the Python constants.

The Python readers stay the source of truth. A schema gives an editor completion and a first check.
Rules that a schema cannot express (a duplicate id, a family count, a missing file) need
`skillz-experiment.pyz validate`. The committed copies live in `skills/skillz/references/schemas/`.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import fields
from typing import Literal, cast

from skillz_experiments._cases import CRITERION_ID, CRITERION_TEXT_LIMIT, JOURNEY_EXPECTED_FIELDS
from skillz_experiments._contract import (
    ARGV_GRADERS, CONTRACT_FIELDS, GRADER_FIELDS, GRADERS, HARNESS_PHRASE_PATTERN, HELPER_FIELDS, RUBRIC_GRADERS, SCHEMA_KEY,
    SKILL_PATTERN, SURFACE_FIELDS, TOOL_PATTERN,
)
from skillz_experiments._evals_format import EVAL_FIELDS, EVAL_KINDS, FIXTURES, JOURNEY_KEYS, JOURNEY_REQUIRED, SOURCES
from skillz_experiments._intake import DRAFT_FIELDS, DRAFT_REQUIRED
from skillz_experiments.gate import Statistics

SchemaName = Literal["evals", "journeys", "contract", "draft"]
NAMES: tuple[SchemaName, ...] = ("evals", "journeys", "contract", "draft")
FILES: dict[SchemaName, str] = {"evals": "evals.schema.json", "journeys": "journeys.schema.json",
                                "contract": "autoimprove.schema.json", "draft": "draft-case.schema.json"}
DIALECT = "https://json-schema.org/draft/2020-12/schema"
REGENERATE = "skillz-experiment.pyz schema {name} > skills/skillz/references/schemas/{file}"

Json = dict[str, object]
NONEMPTY: Json = {"type": "string", "pattern": r"\S"}
NONEMPTY_LIST: Json = {"type": "array", "items": NONEMPTY}
TEXT_MAP: Json = {"type": "object", "additionalProperties": {"type": "string"}}


def regenerate_command(name: SchemaName) -> str:
    return REGENERATE.format(name=name, file=FILES[name])


def _top(title: str, body: Json) -> Json:
    return {"$schema": DIALECT, "title": title} | body


def _pick(properties: Json, names: Sequence[str]) -> Json:
    """Return the property schemas for `names`, in that order. A name with no schema raises `KeyError`."""
    return {name: properties[name] for name in names}


def _any_of(*names: str) -> Json:
    return {"anyOf": [{"required": [name]} for name in names]}


def _eval_entry() -> Json:
    kinds = list(EVAL_KINDS)
    return {
        "type": "object",
        "required": ["id", "name", "prompt", "expected_output", "files", "kind"],
        "properties": _pick({
            "id": {"type": "integer", "description": "Unique whole number."},
            "name": NONEMPTY | {"description": "Unique case name. It becomes the case id."},
            "prompt": NONEMPTY | {"description": "The request that the user sends."},
            "expected_output": NONEMPTY | {"description": "What a good answer does, in prose."},
            "files": {"type": "array", "items": {"type": "string", "pattern": "^" + FIXTURES},
                      "description": f"Fixture files, each under {FIXTURES}."},
            "assertions": NONEMPTY_LIST | {"description": "Checks for a task entry. Other kinds take none."},
            "kind": {"enum": kinds, "description": "trigger: the skill must start. near-miss: it must not. task: it does work."},
            "family": NONEMPTY | {"description": "Group of related cases. Needed for the split."},
            "expected_skill": {"type": ["string", "null"], "description": "The skill that must start, or null for a near-miss."},
        }, EVAL_FIELDS),
        "allOf": [
            {"if": {"properties": {"kind": {"const": "task"}}}, "else": {"properties": {"assertions": {"maxItems": 0}}}},
            {"if": {"properties": {"expected_skill": {"type": "null"}}, "required": ["expected_skill"]},
             "then": {"properties": {"kind": {"const": "near-miss"}}}},
        ],
    }


def evals() -> Json:
    return _top("evals/evals.json", {
        "type": "object",
        "required": ["skill_name", "evals"],
        "properties": {SCHEMA_KEY: {"type": "string"}, "skill_name": NONEMPTY,
                       "evals": {"type": "array", "minItems": 1, "items": _eval_entry()}},
    })


def _criteria() -> Json:
    criterion: Json = {"type": "object", "required": ["id", "text"], "additionalProperties": False, "properties": {
        "id": {"type": "string", "pattern": "^" + CRITERION_ID.pattern + "$"},
        "text": NONEMPTY | {"maxLength": CRITERION_TEXT_LIMIT, "description": "One EARS statement: at most one leading WHEN, WHILE, IF, or WHERE clause, "
                                            + "then one THE <subject> SHALL <response>."},
        "must": {"type": "boolean", "default": False, "description": "A dealbreaker: level 1 or 2 scores the case 0."},
        "from": NONEMPTY | {"maxLength": CRITERION_TEXT_LIMIT, "description": "The source item, such as assertion:2, must:0, questions, or final_stop."}}}
    return {"type": "array", "minItems": 1, "items": criterion, "description": JOURNEY_EXPECTED_FIELDS["criteria"]}


def _expected() -> Json:
    lists = {name: {"type": "array", "items": NONEMPTY, "uniqueItems": True, "description": meaning}
             for name, meaning in JOURNEY_EXPECTED_FIELDS.items() if name not in ("final_stop", "criteria")}
    properties: Json = {**lists, "final_stop": NONEMPTY | {"description": JOURNEY_EXPECTED_FIELDS["final_stop"]},
                        "criteria": _criteria()}
    return {"type": "object", "properties": properties, "additionalProperties": False,
            "anyOf": [{"required": [name], "properties": {name: {"minItems": 1}}} for name in ("must", "must_not")]}


def _journey_properties() -> Json:
    properties: Json = {
        "id": NONEMPTY | {"description": "Unique case id."},
        "family": NONEMPTY | {"description": "Group of related cases. Defaults to the id."},
        "kind": NONEMPTY | {"not": {"enum": list(EVAL_KINDS)}, "description": "A contract kind that uses the journey grader."},
        "request": NONEMPTY | {"description": "The first user message."},
        "brief": NONEMPTY | {"description": "What the simulated user wants and knows."},
        "max_turns": {"type": "integer", "minimum": 1, "default": 8},
        "requirements": {"type": "array", "items": NONEMPTY, "uniqueItems": True},
        "files": TEXT_MAP | {"description": "Inline files: path to text."},
        "expected": _expected(),
        "source": {"enum": list(SOURCES)},
    }
    return {name: properties[name] for name in sorted(JOURNEY_KEYS)}


def journeys() -> Json:
    return _top("evals/journeys.json", {
        "type": "array",
        "minItems": 1,
        "items": {"type": "object", "required": list(JOURNEY_REQUIRED), "properties": _journey_properties(),
                  "additionalProperties": False},
    })


def draft() -> Json:
    properties = {name: value for name, value in _journey_properties().items() if name in DRAFT_FIELDS}
    properties["kind"] = NONEMPTY | {"description": "trigger, near-miss, task, or a journey kind of the contract."}
    properties["source"] = {"enum": list(SOURCES)}
    properties["expected"] = {"description": "The reference. A journey case takes the typed object. A judged task case "
                              + "takes an object with criteria, next to its expected_output and assertions.",
                              "properties": {"criteria": _criteria()}}
    return _top("RUN/cases.draft.json", {
        "type": "array",
        "minItems": 1,
        "items": {"type": "object", "required": sorted(DRAFT_REQUIRED), "properties": properties,
                  "additionalProperties": False},
    })


def _statistics() -> Json:
    properties: Json = {}
    for entry in fields(Statistics):
        metadata = entry.metadata
        if metadata.get("map"):
            number: Json = {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}}
        else:
            bounds = cast(Json, metadata.get("bounds", {"minimum": 0}))
            number = {"type": ["number", "null"] if metadata.get("nullable") else "number"} | bounds
        if "rule" in metadata:
            number["description"] = "Accepts " + cast(str, metadata["rule"]) + "."
        properties[entry.name] = number
    return {"type": "object", "properties": properties, "additionalProperties": False}


def _kind() -> Json:
    has_gate: Json = _any_of("argv", "gate")
    return {
        "type": "object",
        "required": ["grader"],
        "properties": _pick({
            "grader": {"enum": list(GRADERS)},
            "argv": {"type": "array", "minItems": 1, "items": NONEMPTY},
            "gate": NONEMPTY | {"description": "A gate file under evals/gates/, as a relative path. It stands for argv."},
            "rubric": NONEMPTY,
            "capture": {"type": "array", "minItems": 1, "items": NONEMPTY},
        }, GRADER_FIELDS),
        "additionalProperties": False,
        "not": {"required": ["argv", "gate"]},
        "allOf": [
            {"if": {"properties": {"grader": {"enum": list(ARGV_GRADERS)}}, "required": ["grader"]},
             "then": has_gate, "else": {"not": has_gate}},
            {"if": {"properties": {"grader": {"enum": list(RUBRIC_GRADERS)}}, "required": ["grader"]},
             "then": {"required": ["rubric"]}, "else": {"not": _any_of("rubric", "capture")}},
        ],
    }


def _relative_path(suffix: str = "") -> Json:
    return {"type": "string", "minLength": 1, "pattern": r"^[^/.\\][^\\]*" + suffix.replace(".", r"\.") + "$"}


def _helper() -> Json:
    fixture: Json = {
        "type": "object", "required": ["input", "returncode", "output"],
        "properties": {"input": NONEMPTY, "returncode": {"type": "integer"}, "output": {}, "error": {"type": "object"}},
        "additionalProperties": False,
    }
    return {"type": "object", "required": ["path"], "additionalProperties": False, "properties": _pick({
        "path": _relative_path(), "input": _relative_path(), "fixtures": {"type": "array", "items": fixture}},
        HELPER_FIELDS)}


def _surface() -> Json:
    return {"type": "object", "minProperties": 1, "additionalProperties": False, "properties": _pick({
        "tools": {"type": "array", "uniqueItems": True, "items": {"type": "string", "pattern": "^" + TOOL_PATTERN + "$"}},
        "mcp_config": _relative_path(".json"),
        "agents": {"type": "array", "uniqueItems": True, "items": _relative_path(".md")},
        "companions": {"type": "array", "uniqueItems": True, "items": _relative_path()},
    }, SURFACE_FIELDS)}


def contract() -> Json:
    return _top("evals/autoimprove.json", {
        "type": "object",
        "required": ["schema_version", "status", "skill", "invocation", "kinds"],
        "properties": _pick({
            SCHEMA_KEY: {"type": "string", "description": "Editor hint. The runner ignores it."},
            "schema_version": {"const": 1},
            "status": {"enum": ["approved", "draft"], "description": "A draft contract stops a run until the user approves it."},
            "skill": {"type": "string", "pattern": "^" + SKILL_PATTERN + "$"},
            "invocation": NONEMPTY,
            "kinds": {"type": "object", "minProperties": 1, "additionalProperties": _kind()},
            "helper": _helper(),
            "editable": {"type": "array", "items": _relative_path()},
            "surface": _surface(),
            "statistics": _statistics(),
            "harness_phrases": {"type": "array", "items": {"type": "string", "pattern": HARNESS_PHRASE_PATTERN},
                                "description": "Sandbox-only phrases of the eval requests, each of at least two words. "
                                + "A proposal that adds one is a leak."},
        }, (SCHEMA_KEY, *CONTRACT_FIELDS)),
        "additionalProperties": False,
    })


_BUILDERS = {"evals": evals, "journeys": journeys, "contract": contract, "draft": draft}


def schema(name: SchemaName) -> Json:
    return _BUILDERS[name]()


def render(name: SchemaName) -> str:
    """Return the committed text of one schema: sorted-stable JSON with a final newline."""
    return json.dumps(schema(name), indent=2) + "\n"
