"""The proposal leakage check: a proposal may not copy case content into the skill."""
from __future__ import annotations

import dataclasses
from typing import cast

import pytest

from skillz_experiments._candidate import NEW_CLI, Candidate
from skillz_experiments._cases import Audit, Case, Citation, Journey, Label, Split
from skillz_experiments.search import Denylist, Leak, check

SEED = {"SKILL.md": "Review the diff and report each finding with a citation."}
REQUEST = "please rename the quarterly revenue column in the ledger export script today"
WORDS = REQUEST.split()
SPAN = " ".join(WORDS[1:9])
SHORT_SPAN = " ".join(WORDS[1:8])
ANSWER = "the revenue column is named qtr_rev"


def _case(identifier: str = "c1", split: str = "train", request: str = REQUEST,
          files: dict[str, str] | None = None, expected: object = None, journey: Journey | None = None) -> Case:
    return Case(identifier, f"family-{identifier}", cast(Split, split), request,
                {"ledger.csv": "a,b"} if files is None else files,
                {"answer": ANSWER} if expected is None else expected, "test", True, journey=journey)


def _leak(proposal: str, *cases: Case, seed: str = SEED["SKILL.md"]) -> Leak | None:
    return check(Denylist.build(cases or (_case(),)), {"SKILL.md": seed}, {"SKILL.md": proposal})


@pytest.mark.ac("gh-236:AC-1")
def test_a_verbatim_eight_word_request_span_is_a_request_span_leak() -> None:
    assert _leak(f"{SEED['SKILL.md']} Example: {SPAN}.") == Leak("request-span", "SKILL.md")


@pytest.mark.ac("gh-236:AC-1")
def test_a_span_matches_after_case_whitespace_and_edge_punctuation_are_normalized() -> None:
    shouted = "  ".join(f"({word.upper()})," for word in WORDS[1:9])
    assert _leak(f"{SEED['SKILL.md']}\n{shouted}") == Leak("request-span", "SKILL.md")


@pytest.mark.ac("gh-236:AC-1")
def test_a_span_matches_after_unicode_edge_punctuation_is_normalized() -> None:
    curly = " ".join(f"“{word}”—" for word in WORDS[1:9])
    assert _leak(f"{SEED['SKILL.md']} {curly}") == Leak("request-span", "SKILL.md")


@pytest.mark.ac("gh-236:AC-1")
def test_a_validation_case_request_is_in_the_denylist() -> None:
    assert _leak(f"Note: {SPAN}", _case(split="validation")) == Leak("request-span", "SKILL.md")


@pytest.mark.ac("gh-236:AC-1")
def test_the_brief_of_a_journey_case_is_in_the_denylist() -> None:
    brief = "the customer wants a refund for the duplicated charge on invoice nine"
    requirement = "the reply offers a refund to the customer for that duplicated charge"
    journey = Journey(brief, requirements=(requirement,))
    assert _leak(f"Say: {brief}", _case(journey=journey)) == Leak("request-span", "SKILL.md")
    assert _leak(requirement, _case(journey=journey)) is None


@pytest.mark.ac("gh-236:AC-2")
def test_an_expected_string_of_twenty_characters_is_an_expected_answer_leak() -> None:
    assert len(ANSWER) >= 20
    assert _leak(f"{SEED['SKILL.md']} Answer: {ANSWER}") == Leak("expected-answer", "SKILL.md")


@pytest.mark.ac("gh-236:AC-2")
def test_expected_leaves_are_found_in_mappings_lists_and_audit_fields() -> None:
    nested = {"outer": [{"inner": "exactly this deeply nested leaf"}]}
    assert _leak("it says exactly this deeply nested leaf", _case(expected=nested)) == Leak("expected-answer", "SKILL.md")
    audit = Audit((Label("L1", "high", "the loop never terminates on empty input",
                         (Citation("a.py", 1, 1, "while True: pass"),)),))
    assert _leak("the loop never terminates on empty input", _case(expected=audit)) == Leak("expected-answer", "SKILL.md")
    assert _leak("the   LOOP never\nterminates on empty INPUT", _case(expected=audit)) == Leak("expected-answer", "SKILL.md")


@pytest.mark.ac("gh-236:AC-2")
def test_an_expected_string_under_twenty_characters_is_not_in_the_denylist() -> None:
    assert _leak("the answer is forty-two", _case(expected={"answer": "forty-two"})) is None


@pytest.mark.ac("gh-236:AC-3")
def test_a_case_identifier_is_a_case_id_leak() -> None:
    assert _leak(f"{SEED['SKILL.md']} See case-7 for detail.", _case("case-7")) == Leak("case-id", "SKILL.md")


@pytest.mark.ac("gh-236:AC-3")
def test_a_fixture_file_base_name_is_a_fixture_name_leak() -> None:
    case = _case(files={"data/nested/ledger.csv": "a,b"})
    assert _leak(f"{SEED['SKILL.md']} Read ledger.csv first.", case) == Leak("fixture-name", "SKILL.md")
    assert _leak(f"{SEED['SKILL.md']} Read ledger.csv.", case) == Leak("fixture-name", "SKILL.md")


@pytest.mark.ac("gh-236:AC-3")
@pytest.mark.parametrize("proposal", ["see case-70 and xcase-7 and case-7x and case-7-b", "open myledger.csv or ledger.csvx or ledger.csv.bak"])
def test_an_identifier_or_file_name_inside_a_longer_word_is_not_a_leak(proposal: str) -> None:
    assert _leak(proposal, _case("case-7")) is None


@pytest.mark.ac("gh-236:AC-4")
@pytest.mark.parametrize("added", [SPAN, ANSWER, "case-7", "ledger.csv"])
def test_text_the_seed_already_holds_does_not_leak(added: str) -> None:
    seed = f"{SEED['SKILL.md']} {added}"
    assert _leak(f"{seed} A new unrelated sentence.", _case("case-7"), seed=seed) is None


@pytest.mark.ac("gh-236:AC-5")
def test_holdout_content_is_not_in_the_denylist() -> None:
    secret = "the unique holdout marker phrase spans many distinct words here"
    holdout = _case("h1", "holdout", request=secret, files={"holdout-only.csv": "x,y"},
                    expected={"answer": "a holdout-only expected answer string"})
    proposal = f"{secret} h1 holdout-only.csv a holdout-only expected answer string"
    assert _leak(proposal, holdout) is None
    assert _leak(proposal, holdout, _case("t1", request="an unrelated train request with enough distinct words")) is None


@pytest.mark.ac("gh-236:AC-6")
@pytest.mark.parametrize("proposal", [f"Example: {SPAN}.", f"Answer: {ANSWER}", "See case-7.", "Read ledger.csv."])
def test_a_leak_carries_only_the_rule_and_the_component(proposal: str) -> None:
    leak = _leak(proposal, _case("case-7"))
    assert leak is not None
    assert [field.name for field in dataclasses.fields(leak)] == ["rule", "component"]
    text = f"{leak!r} {leak.rule} {leak.component}".lower()
    for secret in (SPAN, ANSWER, "case-7", "ledger.csv"):
        assert secret.lower() not in text


@pytest.mark.ac("gh-236:AC-6")
def test_the_denylist_repr_does_not_print_case_content() -> None:
    text = repr(Denylist.build([_case("case-7")])).lower()
    assert all(secret not in text for secret in (WORDS[2], ANSWER, "case-7", "ledger.csv"))


@pytest.mark.ac("gh-236:AC-7")
def test_a_seven_word_overlap_is_not_a_leak() -> None:
    assert len(SHORT_SPAN.split()) == 7
    assert _leak(f"{SEED['SKILL.md']} {SHORT_SPAN}") is None


@pytest.mark.ac("gh-236:AC-7")
def test_a_common_short_phrase_is_not_a_leak() -> None:
    assert _leak("Please rename the column and report each finding in the script.") is None


def test_the_leak_names_the_changed_component_and_ignores_unchanged_ones() -> None:
    denylist = Denylist.build([_case()])
    seed = {"SKILL.md": SEED["SKILL.md"], "references/a.md": "alpha"}
    assert check(denylist, seed, dict(seed)) is None
    assert check(denylist, seed, {**seed, "references/a.md": f"alpha {SPAN}"}) == Leak("request-span", "references/a.md")
    assert check(denylist, seed, {**seed, "references/new.md": f"beta {SPAN}"}) == Leak("request-span", "references/new.md")


def test_an_empty_denylist_never_leaks() -> None:
    assert check(Denylist.build([]), SEED, {"SKILL.md": f"{SPAN} {ANSWER} case-7 ledger.csv"}) is None


def _seed(files: dict[str, str], *extra: str) -> Candidate:
    """A seed candidate whose editable names are `SKILL.md` and `extra`."""
    return Candidate(dict(files), ("SKILL.md", *extra))


@pytest.mark.ac("gh-302:AC-10")
@pytest.mark.parametrize("added", [
    "No buildable wedge.toml accompanies the supplied skill components.", "Parse the flags with fromargs.",
    "The @new-cli value is empty.", "FromArgs parses the flags."])
def test_a_runner_term_that_a_proposal_adds_to_a_wedge_free_target_is_a_runner_term_leak(added: str) -> None:
    denylist = Denylist.build([_case()], seed=_seed(SEED))
    assert check(denylist, SEED, {"SKILL.md": f"{SEED['SKILL.md']} {added}"}) == Leak("runner-term", "SKILL.md")


@pytest.mark.ac("gh-302:AC-10")
def test_a_runner_term_in_the_seed_exempts_only_that_term() -> None:
    files = {**SEED, "references/a.md": "Parse the flags with fromargs."}
    denylist = Denylist.build([_case()], seed=_seed(files, "references/a.md"))
    assert check(denylist, files, {**files, "SKILL.md": f"{SEED['SKILL.md']} Ship a wedge.toml."}) == Leak("runner-term", "SKILL.md")


@pytest.mark.ac("gh-302:AC-11")
@pytest.mark.parametrize("offered", [{"@wedge/src/tool.py": "import sys\n"}, {NEW_CLI: ""}])
def test_a_runner_term_is_no_leak_when_the_target_offers_a_wedge_source_or_new_cli(offered: dict[str, str]) -> None:
    files = {**SEED, **offered}
    added = {**files, "SKILL.md": f"{SEED['SKILL.md']} Build it from wedge.toml with fromargs and @new-cli."}
    assert check(Denylist.build([_case()], seed=_seed(files, *offered)), files, added) is None
    assert check(Denylist.build([_case()]), SEED, {"SKILL.md": added["SKILL.md"]}) is None


@pytest.mark.ac("gh-302:AC-11")
def test_a_runner_term_is_no_leak_when_any_seed_file_holds_it_or_it_is_part_of_a_longer_token() -> None:
    files = {**SEED, "references/a.md": "See wedge.toml, fromargs, and @new-cli."}
    denylist = Denylist.build([_case()], seed=_seed(files, "references/a.md"))
    assert check(denylist, files, {**files, "SKILL.md": f"{SEED['SKILL.md']} Use fromargs."}) is None
    assert check(denylist, files, {**files, "references/b.md": "Write wedge.toml and @new-cli."}) is None
    assert check(denylist, {}, {"@hypothesis": "Move the fromargs note."}) is None
    longer = {"SKILL.md": f"{SEED['SKILL.md']} Keep mywedge.toml and the fromargs-like parser."}
    assert check(Denylist.build([_case()], seed=_seed(SEED)), SEED, longer) is None
