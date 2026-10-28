"""Property-based checks of the readers of user-written and model-written JSON documents.

The readers are the contract reader `parse`, `parse_evals` for `evals/evals.json`, and `parse_journeys` for
`evals/journeys.json`. The documents come from JSON values that Hypothesis generates, and from well-formed
documents with one or two drawn nodes changed.
"""
from __future__ import annotations

import copy
import hashlib
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillz_experiments._cases import JOURNEY_EXPECTED_FIELDS, CodedError
from skillz_experiments._contract import (ARGV_GRADERS, CONTRACT_FIELDS, GRADER_FIELDS, GRADERS, HELPER_FIELDS,
                                          RUBRIC_GRADERS, SKILL_PATTERN, SURFACE_FIELDS, TOOL_PATTERN, Contract, Grader,
                                          Helper, Surface, parse)
from skillz_experiments._evals_format import (EVAL_FIELDS, EVAL_KINDS, FIXTURES, JOURNEY_KEYS, SOURCES, EvalEntry,
                                              ParsedEvals, ParsedJourneys, parse_evals, parse_journeys)
from skillz_experiments.gate import Statistics

CONTRACT_CODES = frozenset({"contract-unapproved", "contract-kinds", "contract-rubric-field", "contract-surface",
                            "contract-pillars-removed", "contract-statistics-invalid", "contract-harness-phrases"})

# Words that the readers give a meaning to, so that a changed value often passes one check and fails a later one.
VOCABULARY = st.sampled_from([*GRADERS, *EVAL_KINDS, *SOURCES, "approved", "draft", "reference.must", "reference.bogus",
                              "evals/files/a", "../up", ".hidden", "two words"])
JSON: st.SearchStrategy[object] = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=8) | VOCABULARY,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=6), inner, max_size=3), max_leaves=8)
# The field names that the readers know, so that a change that adds a key often adds a meaningful one.
FIELD_NAMES = st.sampled_from(sorted({*CONTRACT_FIELDS, *GRADER_FIELDS, *HELPER_FIELDS, *SURFACE_FIELDS, *EVAL_FIELDS,
                                      *JOURNEY_KEYS, *JOURNEY_EXPECTED_FIELDS, *Statistics.names(), "$schema",
                                      "evals", "skill_name", "pillars", "returncode", "output", "error"})) | st.text(max_size=8)
# Text that `str.strip` keeps: at least one character that is not whitespace.
NONEMPTY = st.tuples(st.text(max_size=6), st.characters(exclude_categories=("Z", "Cc", "Cs")),
                     st.text(max_size=6)).map("".join)
SEGMENTS = st.from_regex(r"[a-z0-9_][a-z0-9_-]{0,7}", fullmatch=True)


def _first(drawn: tuple[object, object]) -> object:
    """Return the document of a drawn document and its expected reading."""
    return drawn[0]


def _paths(root: str) -> st.SearchStrategy[str]:
    """Return relative, non-hidden paths under `root`."""
    return st.lists(SEGMENTS, min_size=1, max_size=2).map(lambda parts: "/".join([root, *parts]))


def _nodes(value: object, path: tuple[str | int, ...] = ()) -> list[tuple[tuple[str | int, ...], object]]:
    """Return the path and value of every node of a JSON value, the root first."""
    nodes: list[tuple[tuple[str | int, ...], object]] = [(path, value)]
    if isinstance(value, dict):
        for key, item in cast(dict[str, object], value).items():
            nodes.extend(_nodes(item, (*path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(cast(list[object], value)):
            nodes.extend(_nodes(item, (*path, index)))
    return nodes


def _child(container: object, step: str | int) -> object:
    if isinstance(container, dict):
        return cast(dict[str | int, object], container)[step]
    return cast(list[object], container)[cast(int, step)]


def _scrambled(node: tuple[tuple[str | int, ...], object]) -> bytes:
    return hashlib.blake2b(repr(node).encode(), digest_size=8).digest()


def _mutate(draw: st.DrawFn, document: object) -> object:
    """Return a copy of `document` with one node below the root replaced, dropped, or given a sibling.

    Hypothesis favors the first choices of `st.sampled_from`. The nodes come in an order set by a digest of each path
    and value, so the favored node changes with the document, and a node at any depth can be the target.
    """
    document = copy.deepcopy(document)
    nodes = [path for path, _ in sorted(_nodes(document)[1:], key=_scrambled)]
    if not nodes:
        return draw(JSON)
    *steps, last = draw(st.sampled_from(nodes))
    parent = document
    for step in steps:
        parent = _child(parent, step)
    action = draw(st.sampled_from(["replace", "drop", "sibling"]))
    if isinstance(parent, dict):
        table = cast(dict[str | int, object], parent)
        if action == "replace":
            table[last] = draw(JSON)
        elif action == "drop":
            del table[last]
        else:
            table[draw(FIELD_NAMES)] = draw(JSON)
    else:
        items = cast(list[object], parent)
        if action == "replace":
            items[cast(int, last)] = draw(JSON)
        elif action == "drop":
            del items[cast(int, last)]
        else:
            items.append(items[cast(int, last)])
    return document


@st.composite
def _mutated(draw: st.DrawFn, documents: st.SearchStrategy[object]) -> object:
    """Draw a document, then change one or two of its nodes."""
    document = draw(documents)
    for _ in range(draw(st.integers(1, 2))):
        document = _mutate(draw, document)
    return document


KIND_NAMES = st.just("journey") | st.from_regex(r"[a-z][a-z-]{0,10}", fullmatch=True)
ARGVS = st.lists(NONEMPTY, min_size=1, max_size=3)
RUBRICS = NONEMPTY | st.sampled_from(sorted(JOURNEY_EXPECTED_FIELDS)).map(lambda field: f"Grade reference.{field}.")
TOOLS = st.from_regex(TOOL_PATTERN, fullmatch=True)
WORDS = st.from_regex(r"[A-Za-z0-9_]{1,6}", fullmatch=True)
PHRASES = st.tuples(WORDS, st.sampled_from([" ", "\t", " and "]), WORDS).map("".join)
STATISTICS = st.fixed_dictionaries({}, optional={
    "min_gain": st.integers(0, 5) | st.floats(0, 5), "min_coverage": st.floats(0, 1),
    "min_token_saving": st.none() | st.floats(0, 1, exclude_min=True, exclude_max=True),
    "family_budgets": st.dictionaries(NONEMPTY, st.floats(0, 5), max_size=2)})


def _threshold(value: object) -> object:
    """Return the value that the statistics reader keeps for a valid threshold or budget map."""
    if isinstance(value, dict):
        return {key: float(number) for key, number in cast(dict[str, float], value).items()}
    return None if value is None else float(cast(float, value))


@st.composite
def _fixtures(draw: st.DrawFn) -> dict[str, object]:
    returncode = draw(st.integers(-3, 3))
    output = draw(st.none() | st.integers() | st.text(max_size=8))
    fixture: dict[str, object] = {"input": draw(NONEMPTY), "returncode": returncode, "output": output}
    if (output is None and returncode != 0) or draw(st.booleans()):
        fixture["error"] = draw(st.dictionaries(st.text(max_size=5), st.integers(), max_size=2))
    return fixture


@st.composite
def _surface(draw: st.DrawFn) -> tuple[dict[str, object], Surface]:
    tools = draw(st.lists(TOOLS, max_size=2, unique=True))
    config = draw(st.none() | SEGMENTS.map(lambda part: f"mcp/{part}.json"))
    agents = [f"agents/{part}.md" for part in draw(st.lists(SEGMENTS, max_size=2, unique=True))]
    companions = [f"companions/{part}" for part in draw(st.lists(SEGMENTS, max_size=2, unique=True))]
    if not (tools or config or agents or companions):
        tools = [draw(TOOLS)]
    document: dict[str, object] = {"tools": tools, "agents": agents, "companions": companions}
    if config is not None:
        document["mcp_config"] = config
    return document, Surface(tuple(tools), config, tuple(agents), tuple(companions))


@st.composite
def _contracts(draw: st.DrawFn) -> tuple[dict[str, object], Contract]:
    """Draw a well-formed contract document and the contract that it declares."""
    kinds: dict[str, object] = {}
    graders: dict[str, Grader] = {}
    for kind in draw(st.lists(KIND_NAMES, min_size=1, max_size=3, unique=True)):
        grader = "journey" if kind == "journey" else draw(st.sampled_from(GRADERS))
        argv = draw(ARGVS) if grader in ARGV_GRADERS else []
        rubric = draw(RUBRICS) if grader in RUBRIC_GRADERS else ""
        capture = draw(st.just([]) | ARGVS) if grader in RUBRIC_GRADERS else []
        entry: dict[str, object] = {"grader": grader}
        for field, value in (("argv", argv), ("rubric", rubric), ("capture", capture)):
            if value:
                entry[field] = value
        kinds[kind] = entry
        graders[kind] = Grader(grader, tuple(argv), rubric, tuple(capture))
    skill, invocation = draw(st.from_regex(SKILL_PATTERN, fullmatch=True)), draw(NONEMPTY)
    document: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": skill, "invocation": invocation,
                                   "kinds": kinds}
    if draw(st.booleans()):
        document["$schema"] = draw(st.text(max_size=10))
    editable = draw(st.lists(_paths("edit"), max_size=3))
    if editable or draw(st.booleans()):
        document["editable"] = editable
    helper = None
    if draw(st.booleans()):
        fixtures = draw(st.lists(_fixtures(), max_size=2))
        helper = Helper(draw(_paths("helper")), draw(_paths("helper")), tuple(fixtures))
        document["helper"] = {"path": helper.path, "input": helper.input, "fixtures": fixtures}
    surface = None
    if draw(st.booleans()):
        document["surface"], surface = draw(_surface())
    statistics = None
    if raw := draw(st.none() | STATISTICS):
        document["statistics"] = raw
        statistics = {name: _threshold(value) for name, value in raw.items()}
    phrases = draw(st.lists(PHRASES, max_size=2))
    if phrases or draw(st.booleans()):
        document["harness_phrases"] = phrases
    source = draw(st.sampled_from(["skill", "intake"]))
    return document, Contract(skill, invocation, graders, helper, tuple(editable), source, surface, statistics,
                              tuple(phrases))


@st.composite
def _evals(draw: st.DrawFn) -> tuple[dict[str, object], tuple[EvalEntry, ...]]:
    """Draw a well-formed strict `evals.json` document and the entries that it holds."""
    size = draw(st.integers(1, 4))
    names = draw(st.lists(NONEMPTY, min_size=size, max_size=size, unique=True))
    base, step = draw(st.integers()), draw(st.integers(1, 1000))
    ids = [base + step * index for index in range(size)]
    items: list[object] = []
    entries: list[EvalEntry] = []
    for index, (name, ident) in enumerate(zip(names, ids, strict=True)):
        kind = draw(st.sampled_from(EVAL_KINDS))
        prompt, expected_output = draw(NONEMPTY), draw(NONEMPTY)
        files = draw(st.lists(SEGMENTS.map(lambda part: FIXTURES + part), max_size=2))
        item: dict[str, object] = {"id": ident, "name": name, "prompt": prompt, "expected_output": expected_output,
                                   "files": files, "kind": kind}
        assertions = draw(st.lists(NONEMPTY, max_size=2)) if kind == "task" else []
        if assertions or draw(st.booleans()):
            item["assertions"] = assertions
        family = draw(st.none() | NONEMPTY)
        if family is not None:
            item["family"] = family
        match draw(st.sampled_from(["absent", "named", "null"] if kind == "near-miss" else ["absent", "named"])):
            case "named":
                item["expected_skill"] = draw(NONEMPTY)
            case "null":
                item["expected_skill"] = None
            case _:
                pass
        items.append(item)
        entries.append(EvalEntry(index, name, prompt, kind, False, family, tuple(files), expected_output, tuple(assertions)))
    return {"skill_name": draw(NONEMPTY), "evals": items}, tuple(entries)


@st.composite
def _journeys(draw: st.DrawFn) -> tuple[list[object], tuple[dict[str, object], ...]]:
    """Draw a well-formed strict `journeys.json` document and the draft cases that it holds."""
    size = draw(st.integers(1, 3))
    items: list[object] = []
    cases: list[dict[str, object]] = []
    for ident in draw(st.lists(NONEMPTY, min_size=size, max_size=size, unique=True)):
        texts = st.lists(NONEMPTY, max_size=2, unique=True)
        expected: dict[str, object] = {"must": draw(st.lists(NONEMPTY, min_size=1, max_size=2, unique=True))}
        for key in ("must_not", "questions"):
            if draw(st.booleans()):
                expected[key] = draw(texts)
        if draw(st.booleans()):
            expected["final_stop"] = draw(NONEMPTY)
        item: dict[str, object] = {"id": ident, "family": draw(NONEMPTY), "kind": "journey-" + draw(NONEMPTY),
                                   "request": draw(NONEMPTY), "brief": draw(NONEMPTY), "expected": expected,
                                   "source": draw(st.sampled_from(SOURCES))}
        case = {key: item[key] for key in ("id", "family", "kind", "request", "source", "brief", "expected")}
        case["max_turns"] = 8
        if draw(st.booleans()):
            item["max_turns"] = case["max_turns"] = draw(st.integers(1, 50))
        requirements = draw(texts)
        if requirements or draw(st.booleans()):
            item["requirements"] = requirements
        if requirements:
            case["requirements"] = requirements
        if draw(st.booleans()):
            item["files"] = case["files"] = draw(st.dictionaries(_paths("fixtures"), NONEMPTY, max_size=2))
        items.append(item)
        cases.append(case)
    return items, tuple(cases)


def _contract_outcome(value: object) -> Contract | str:
    """Return the contract, the code of its `CodedError`, or the name of the `ValueError` type. Other errors fail the test."""
    try:
        return parse(value, "skill")
    except ValueError as error:
        return error.code if isinstance(error, CodedError) else type(error).__name__


_BASE: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "demo", "invocation": "$demo run",
                            "kinds": {"task": {"grader": "judge", "rubric": "Grade the answer."}}}
# The changed documents of the property below reach few of these codes in 100 examples, so each code also has a fixed case.
_CODED: list[tuple[dict[str, object], str]] = [
    (_BASE | {"status": "draft"}, "contract-unapproved"),
    (_BASE | {"kinds": {"journey": {"grader": "judge", "rubric": "Grade it."}}}, "contract-kinds"),
    (_BASE | {"kinds": {"walk": {"grader": "journey", "argv": ["run"], "rubric": "See reference.bogus."}}},
     "contract-rubric-field"),
    (_BASE | {"kinds": {"task": {"grader": "judge", "rubric": "Grade it.", "pillars": list[str]()}}},
     "contract-pillars-removed"),
    (_BASE | {"surface": dict[str, object]()}, "contract-surface"),
    (_BASE | {"statistics": {"min_gain": -1}}, "contract-statistics-invalid"),
    (_BASE | {"harness_phrases": ["one"]}, "contract-harness-phrases"),
]


@given(_mutated(_contracts().map(_first)) | JSON)
def test_the_contract_reader_returns_a_contract_or_raises_a_value_error_with_a_known_code(value: object) -> None:
    outcome = _contract_outcome(value)

    assert isinstance(outcome, Contract) or outcome in CONTRACT_CODES | {"ValueError"}


@pytest.mark.parametrize(("document", "code"), _CODED)
def test_each_contract_code_comes_from_a_document_built_for_it(document: dict[str, object], code: str) -> None:
    assert code in CONTRACT_CODES
    assert _contract_outcome(document) == code


@given(_contracts())
def test_a_well_formed_contract_is_read_with_its_fields_and_reads_back_from_its_data(
        drawn: tuple[dict[str, object], Contract]) -> None:
    document, contract = drawn

    assert parse(document, contract.source) == contract
    assert parse(contract.data(), contract.source) == contract


@given(st.booleans(), _mutated(_evals().map(_first)) | JSON)
def test_parse_evals_returns_each_entry_or_errors_at_its_location(strict: bool, document: object) -> None:
    parsed = parse_evals(document, strict=strict)

    evals = cast(dict[str, object], document).get("evals") if isinstance(document, dict) else None
    if not isinstance(evals, list) or (strict and not evals):
        assert parsed.entries == ()
        assert parsed.errors
        return
    indices = [entry.index for entry in parsed.entries]
    assert indices == sorted(set(indices))
    assert len({entry.identifier for entry in parsed.entries}) == len(indices)
    for index in range(len(cast(list[object], evals))):
        assert (index in indices) != any(error.startswith(f"evals[{index}]: ") for error in parsed.errors)


@given(_evals(), st.booleans())
def test_a_well_formed_evals_document_is_read_with_its_fields(
        drawn: tuple[dict[str, object], tuple[EvalEntry, ...]], strict: bool) -> None:
    document, entries = drawn

    assert parse_evals(document, strict=strict) == ParsedEvals(entries, ())


@given(_evals().map(_first) | _mutated(_evals().map(_first)))
def test_an_evals_document_that_strict_accepts_gives_the_same_result_lenient(document: object) -> None:
    strict = parse_evals(document, strict=True)

    if not strict.errors:
        assert parse_evals(document, strict=False) == strict


@given(st.booleans(), _mutated(_journeys().map(_first)) | JSON)
def test_parse_journeys_returns_each_case_or_one_error_at_its_location(strict: bool, document: object) -> None:
    parsed = parse_journeys(document, strict=strict)

    if not isinstance(document, list) or (strict and not document):
        assert parsed.cases == ()
        assert len(parsed.errors) == 1
        return
    items = cast(list[object], document)
    located = [index for index in range(len(items)) if any(error.startswith(f"journeys[{index}]: ") for error in parsed.errors)]
    assert len(located) == len(parsed.errors)
    assert len(parsed.cases) + len(parsed.errors) == len(items)
    assert len({case["id"] for case in parsed.cases}) == len(parsed.cases)


@given(_journeys(), st.booleans())
def test_a_well_formed_journeys_document_is_read_with_its_fields(
        drawn: tuple[list[object], tuple[dict[str, object], ...]], strict: bool) -> None:
    document, cases = drawn

    assert parse_journeys(document, strict=strict) == ParsedJourneys(cases, ())


@given(_journeys().map(_first) | _mutated(_journeys().map(_first)))
def test_a_journeys_document_that_strict_accepts_gives_the_same_result_lenient(document: object) -> None:
    strict = parse_journeys(document, strict=True)

    if not strict.errors:
        assert parse_journeys(document, strict=False) == strict
