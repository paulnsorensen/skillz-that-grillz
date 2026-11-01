"""Generated JSON Schemas for the author files (gh-370): drift, parity with the Python readers, and the format page."""
# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator

from skillz_experiments._cases import JOURNEY_EXPECTED_FIELDS, CodedError
from skillz_experiments._cli import main
from skillz_experiments._contract import CONTRACT_FIELDS, GRADER_FIELDS, HELPER_FIELDS, SCHEMA_KEY, SURFACE_FIELDS, load_contract, parse
from skillz_experiments._evals_format import EVAL_FIELDS, JOURNEY_KEYS, parse_evals, parse_journeys
from skillz_experiments._gate_files import expand_gates
from skillz_experiments._intake import DRAFT_FIELDS, load_draft
from skillz_experiments._schemas import FILES, NAMES, SchemaName, regenerate_command, render, schema
from skillz_experiments.gate import Statistics

ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / "skills"
SCHEMAS = SKILLS / "skillz/references/schemas"
FORMATS = SKILLS / "skillz/references/eval-formats.md"
AUTHOR_FILES: dict[SchemaName, str] = {"evals": "evals", "contract": "autoimprove", "journeys": "journeys"}

CRITERIA = [{"id": "refuses", "text": "THE agent SHALL refuse entry.", "from": "must:0"}]
JOURNEY: dict[str, object] = {
    "id": "age-1", "family": "age-gate", "kind": "journey", "request": "/age-check Is a person of 17 allowed in?",
    "brief": "You run a venue. The guest is 17.", "max_turns": 4, "requirements": ["AGE-1"],
    "files": {"venue/rules.md": "Entry is for ages 18 and up.\n"},
    "expected": {"must": ["refuses entry"], "must_not": ["asks for a name"], "criteria": CRITERIA}, "source": "skill",
}
EVAL: dict[str, object] = {"id": 1, "name": "rename", "prompt": "Rename the note.", "expected_output": "The note is renamed.",
                           "files": ["evals/files/note.txt"], "kind": "task", "assertions": ["The note has a new name."],
                           "family": "rename"}
CONTRACT: dict[str, object] = {
    "schema_version": 1, "status": "approved", "skill": "demo", "invocation": "/demo", "editable": ["SKILL.md"],
    "kinds": {"task": {"grader": "judge", "rubric": "Grade the answer."},
              "check": {"grader": "command", "argv": ["python3", "-c", "print(1)"]}},
    "statistics": {"min_coverage": 0.5, "max_quality_loss": None, "family_budgets": {"a": 2}},
}
DRAFT: dict[str, object] = {"id": "c1", "family": "f1", "kind": "task", "request": "Rewrite the note.",
                            "files": {"note.txt": "hello\n"}, "expected": {"text": "hello"}, "source": "skill"}


def _with(base: dict[str, object], **changes: object) -> dict[str, object]:
    document = copy.deepcopy(base)
    for key, value in changes.items():
        if value is None:
            _ = document.pop(key, None)
        else:
            document[key] = value
    return document


def _kinds(**kinds: object) -> dict[str, object]:
    return _with(CONTRACT, kinds=kinds)


def _expected(**expected: object) -> dict[str, object]:
    return _with(JOURNEY, expected={"criteria": CRITERIA} | expected)


# (schema name, document, should pass). Each document goes through the schema and the Python reader.
EXAMPLES: list[tuple[SchemaName, object, bool, str]] = [
    ("evals", {"skill_name": "demo", "evals": [EVAL]}, True, "evals-good"),
    ("evals", {SCHEMA_KEY: "x.json", "skill_name": "demo", "evals": [EVAL]}, True, "evals-with-schema-key"),
    ("evals", {"skill_name": "demo", "evals": [_with(EVAL, expected_output=None)]}, False, "evals-no-expected-output"),
    ("evals", {"skill_name": "demo", "evals": [_with(EVAL, kind="trigger")]}, False, "evals-assertions-on-trigger"),
    ("evals", {"skill_name": "demo", "evals": [_with(EVAL, kind="other")]}, False, "evals-unknown-kind"),
    ("evals", {"skill_name": "demo", "evals": [_with(EVAL, files=["elsewhere/note.txt"])]}, False, "evals-fixture-path"),
    ("evals", {"skill_name": "demo", "evals": []}, False, "evals-empty"),
    ("journeys", [JOURNEY], True, "journeys-good"),
    ("journeys", [_expected(final_stop="done", must=["a"])], True, "journeys-final-stop"),
    ("journeys", [_expected(must=["a"], bogus=["b"])], False, "journeys-unknown-expected-key"),
    ("journeys", [_expected(questions=["a"])], False, "journeys-expected-needs-must"),
    ("journeys", [_with(JOURNEY, expected={"must": ["a"]})], True, "journeys-seed-without-criteria"),
    ("journeys", [_expected(must=["a"], criteria=[{"id": "a", "text": "t", "weight": 1}])], False, "journeys-criterion-field"),
    ("journeys", [_with(JOURNEY, kind="task")], False, "journeys-eval-kind"),
    ("journeys", [_with(JOURNEY, brief=None)], False, "journeys-no-brief"),
    ("journeys", [_with(JOURNEY, max_turns=0)], False, "journeys-zero-turns"),
    ("journeys", [_with(JOURNEY, source="session")], True, "journeys-session-source"),
    ("journeys", [_with(JOURNEY, source="chat")], False, "journeys-source"),
    ("journeys", [], False, "journeys-empty"),
    ("contract", CONTRACT, True, "contract-good"),
    ("contract", _with(CONTRACT, **{SCHEMA_KEY: "../references/schemas/autoimprove.schema.json"}), True, "contract-with-schema-key"),
    ("contract", _with(CONTRACT, extra=1), False, "contract-unknown-field"),
    ("contract", _kinds(task={"grader": "command"}), False, "contract-command-without-argv"),
    ("contract", _kinds(task={"grader": "exact-json", "argv": ["x"]}), False, "contract-argv-on-exact-json"),
    ("contract", _kinds(task={"grader": "judge"}), False, "contract-judge-without-rubric"),
    ("contract", _kinds(task={"grader": "audit", "rubric": "r"}), False, "contract-rubric-on-audit"),
    ("contract", _kinds(task={"grader": "judge", "rubric": "r", "pillars": ["accuracy"]}), False, "contract-pillars-removed"),
    ("contract", _with(CONTRACT, statistics={"min_coverage": 2}), False, "contract-coverage-range"),
    ("contract", _with(CONTRACT, harness_phrases=["do not run git or gh", "Dry run"]), True, "contract-harness-phrases-good"),
    ("contract", _with(CONTRACT, harness_phrases=[]), True, "contract-harness-phrases-empty"),
    ("contract", _with(CONTRACT, harness_phrases=["dry"]), False, "contract-harness-phrase-one-word"),
    ("contract", _with(CONTRACT, harness_phrases=["dry run", ""]), False, "contract-harness-phrase-empty-string"),
    ("contract", _with(CONTRACT, harness_phrases=["- run"]), False, "contract-harness-phrase-punctuation-word"),
    ("contract", _with(CONTRACT, harness_phrases="dry run"), False, "contract-harness-phrases-not-a-list"),
    ("contract", _with(CONTRACT, harness_phrases=[7]), False, "contract-harness-phrase-not-a-string"),
    ("contract", _with(CONTRACT, statistics={"min_token_saving": 1}), False, "contract-saving-range"),
    ("contract", _with(CONTRACT, statistics={"bogus": 1}), False, "contract-unknown-statistic"),
    ("contract", _with(CONTRACT, skill="Demo"), False, "contract-skill-name"),
    ("contract", _with(CONTRACT, schema_version=2), False, "contract-version"),
    ("contract", _with(CONTRACT, kinds={}), False, "contract-no-kinds"),
    ("draft", [DRAFT], True, "draft-good"),
    ("draft", [_with(DRAFT, bogus=1)], False, "draft-unknown-field"),
    ("draft", [_with(DRAFT, source="other")], False, "draft-source"),
    ("draft", [_with(DRAFT, family=None)], False, "draft-no-family"),
    ("draft", [], False, "draft-empty"),
    ("draft", [_with(DRAFT, files=["in.txt"])], False, "draft-legacy-unresolved-fixture"),
    ("draft", [_with(DRAFT, kind="trigger", files=[])], True, "draft-legacy-empty-files"),
    ("draft", {"cases": [DRAFT]}, True, "draft-object-inline-files"),
    ("draft", {"fixtures": {"in.txt": "t"}, "cases": [_with(DRAFT, files=["in.txt"])]}, True, "draft-shared-fixtures"),
    ("draft", {"fixtures": {"in.txt": "t"}, "cases": [_with(DRAFT, files={"in.txt": None, "local.txt": "local"})]},
     True, "draft-mixed-fixtures"),
    ("draft", [_with(DRAFT, files={"in.txt": None})], False, "draft-legacy-null-reference"),
    ("draft", {"fixtures": {"in.txt": None}, "cases": [DRAFT]}, False, "draft-null-fixture-text"),
    ("draft", {"fixtures": {"in.txt": "t"}, "cases": [_with(DRAFT, files={"in.txt": None, "local.txt": 7})]},
     False, "draft-mixed-invalid-text"),
    ("draft", {"cases": [DRAFT], "bogus": 1}, False, "draft-shared-unknown-key"),
    ("draft", {"fixtures": {}, "cases": []}, False, "draft-shared-empty"),
]


def _python_accepts(name: SchemaName, document: object, scratch: Path) -> bool:
    try:
        if name == "evals":
            return not parse_evals(document, strict=True).errors
        if name == "journeys":
            return not parse_journeys(document, strict=True).errors
        if name == "contract":
            _ = parse(expand_gates(document, scratch), "skill")
            return True
        path = scratch / "cases.draft.json"
        _ = path.write_text(json.dumps(document), encoding="utf-8")
        _ = load_draft(path)
        return True
    except (ValueError, CodedError):
        return False


def _schema_accepts(name: SchemaName, document: object) -> bool:
    return Draft202012Validator(schema(name)).is_valid(document)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("name", NAMES)
def test_every_generated_schema_is_a_valid_draft_2020_12_schema(name: SchemaName) -> None:
    Draft202012Validator.check_schema(schema(name))


@pytest.mark.ac("gh-370:AC-6")
@pytest.mark.parametrize("name", NAMES)
def test_the_committed_schema_equals_the_schema_command_output(name: SchemaName, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", name]) == 0
    printed = capsys.readouterr().out
    committed = (SCHEMAS / FILES[name]).read_text(encoding="utf-8")
    assert committed == printed == render(name), f"{FILES[name]} is stale; run: {regenerate_command(name)}"


@pytest.mark.ac("gh-370:AC-13")
def test_each_generated_object_takes_its_property_names_from_the_field_tuple_of_its_reader() -> None:
    def keys(node: object) -> set[str]:
        return set(cast(dict[str, object], cast(dict[str, object], node)["properties"]))

    top = cast(dict[str, dict[str, object]], schema("contract")["properties"])
    kind = cast(dict[str, object], top["kinds"]["additionalProperties"])
    entry = cast(dict[str, dict[str, object]], schema("evals")["properties"])["evals"]["items"]
    assert keys(schema("contract")) == {SCHEMA_KEY, *CONTRACT_FIELDS}
    assert keys(kind) == set(GRADER_FIELDS)
    assert keys(top["helper"]) == set(HELPER_FIELDS)
    assert keys(top["surface"]) == set(SURFACE_FIELDS)
    assert keys(entry) == set(EVAL_FIELDS)
    assert keys(top["statistics"]) == set(Statistics.names())


@pytest.mark.ac("gh-370:AC-5")
def test_a_contract_with_a_schema_key_loads_with_the_same_identity() -> None:
    plain = parse(copy.deepcopy(CONTRACT), "skill")
    marked = parse(_with(CONTRACT, **{SCHEMA_KEY: "../references/schemas/autoimprove.schema.json"}), "skill")
    assert marked.identity == plain.identity
    assert SCHEMA_KEY not in marked.data()


@pytest.mark.ac("gh-370:AC-7")
@pytest.mark.parametrize("name", ["evals", "contract"])
def test_every_committed_author_file_names_its_schema_and_passes_schema_and_loader(name: SchemaName) -> None:
    files = sorted(SKILLS.glob(f"*/evals/{AUTHOR_FILES[name]}.json"))
    assert files
    for path in files:
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        reference = document.get(SCHEMA_KEY)
        assert isinstance(reference, str), f"{path} needs a relative {SCHEMA_KEY}"
        assert (path.parent / reference).resolve() == (path.parents[1] / "references/schemas" / FILES[name]).resolve()
        assert (path.parent / reference).is_file()
        assert _schema_accepts(name, document), path
        assert _python_accepts(name, document, path.parents[1]), path
    if name == "contract":
        for path in files:
            _ = load_contract(path.parents[1])


@pytest.mark.ac("gh-370:AC-7")
def test_every_committed_journeys_file_passes_schema_and_loader_and_carries_no_schema_key() -> None:
    files = sorted(SKILLS.glob("*/evals/journeys.json"))
    assert files
    for path in files:
        document = cast(object, json.loads(path.read_text(encoding="utf-8")))
        assert isinstance(document, list), "journeys.json is a bare list, so it has no place for a schema key"
        assert _schema_accepts("journeys", document), path
        assert _python_accepts("journeys", document, path.parents[1]), path


@pytest.mark.ac("gh-370:AC-7")
@pytest.mark.parametrize(("name", "document", "good"), [example[:3] for example in EXAMPLES],
                         ids=[example[3] for example in EXAMPLES])
def test_schema_and_python_reader_agree_on_the_shared_examples(
        name: SchemaName, document: object, good: bool, tmp_path: Path) -> None:
    assert _schema_accepts(name, document) is good
    assert _python_accepts(name, document, tmp_path) is good


# The format page


def _page() -> str:
    return FORMATS.read_text(encoding="utf-8")


@pytest.mark.ac("gh-370:AC-8")
def test_the_format_page_names_every_field_of_every_author_and_draft_format() -> None:
    page = _page()
    fields = {"evals": EVAL_FIELDS, "journeys": sorted(JOURNEY_KEYS), "draft": sorted(DRAFT_FIELDS),
              "contract": CONTRACT_FIELDS, "kind": GRADER_FIELDS, "helper": HELPER_FIELDS, "surface": SURFACE_FIELDS,
              "expected": tuple(JOURNEY_EXPECTED_FIELDS), "statistics": Statistics.names()}
    missing = [f"{group}.{field}" for group, names in fields.items() for field in names if f"`{field}`" not in page]
    assert not missing, missing


def test_the_format_page_names_each_schema_file_and_the_validate_command() -> None:
    page = _page()
    assert all(f"`{file}`" in page or file in page for file in FILES.values())
    assert "validate" in page and SCHEMA_KEY in page


@pytest.mark.ac("gh-370:AC-11")
def test_the_published_skill_ships_no_mode_fixture_manifest() -> None:
    assert not (SKILLS / "skillz/evals/mode-fixtures.json").exists()
    assert not (SKILLS / "skillz/evals/gates/mode").exists()
    assert (Path(__file__).parent / "fixtures/mode/mode-fixtures.json").is_file()
