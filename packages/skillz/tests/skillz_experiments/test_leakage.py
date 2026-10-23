"""The proposal leakage check: a proposal may not copy case content into the skill."""
from __future__ import annotations

import dataclasses
from typing import cast

import pytest

from skillz_experiments._candidate import NEW_CLI, Candidate
from skillz_experiments._cases import Audit, Case, Citation, Journey, Label, Split
from skillz_experiments.search import Denylist, HarnessText, Leak, check

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


@pytest.mark.ac("skillz-search-token-tie:AC-5")
def test_a_fixture_name_that_a_seed_file_holds_is_target_vocabulary_in_every_component() -> None:
    case = _case(files={"VERSION": "1.0.0", "commits.txt": "fix: x"})
    seed = Candidate({"SKILL.md": "Read `VERSION` and `commits.txt`.", NEW_CLI: ""}, ("SKILL.md", NEW_CLI))
    module = 'name = "next-version"\nmodule = """Read the version and commits.txt."""'
    assert check(Denylist.build((case,), seed=seed), seed.files, {NEW_CLI: module}) is None
    assert check(Denylist.build((case,)), seed.files, {NEW_CLI: module}) == Leak("fixture-name", NEW_CLI)
    other = Candidate({"SKILL.md": "Read the files."}, ("SKILL.md",))
    assert check(Denylist.build((case,), seed=other), other.files, {"SKILL.md": "Read commits.txt."}) == Leak("fixture-name", "SKILL.md")


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


def _harness(proposal: str, *cases: Case, files: dict[str, str] | None = None, phrases: tuple[str, ...] = ()) -> Leak | None:
    """Check a proposal that adds `proposal` to `SKILL.md` of a seed whose files are `files`."""
    held = dict(SEED if files is None else files)
    denylist = Denylist.build(cases, seed=_seed(held, *(name for name in held if name != "SKILL.md")), phrases=phrases)
    return check(denylist, held, {**held, "SKILL.md": f"{held['SKILL.md']} {proposal}"})


@pytest.mark.ac("gh-499:AC-1")
@pytest.mark.parametrize("split", ["train", "validation"])
def test_a_request_path_that_no_seed_file_holds_is_a_harness_token_leak(split: str) -> None:
    case = _case(split=split, request="write the plan to output/plan.json and stop")
    assert _harness("Write the plan to output/plan.json.", case) == Leak("harness-token", "SKILL.md")
    assert _harness("Write the plan to the plan file.", case) is None


@pytest.mark.ac("gh-499:AC-2")
def test_a_declared_phrase_leaks_after_case_whitespace_and_punctuation_are_normalized() -> None:
    phrases = ("do not run git or gh",)
    assert _harness("Do not run git or gh.", _case(), phrases=phrases) == Leak("harness-token", "SKILL.md")
    assert _harness("DO  NOT\nrun git or GH!", _case(), phrases=phrases) == Leak("harness-token", "SKILL.md")
    assert _harness("Do not run git or gh.", _case()) is None
    assert _harness("Do not run git or ghost tools.", _case(), phrases=phrases) is None


@pytest.mark.ac("gh-499:AC-3")
def test_a_harness_path_that_any_seed_file_holds_is_target_vocabulary() -> None:
    case = _case(request="save the report as output/report.md for review")
    files = {**SEED, "reference.md": "The report goes to output/report.md."}
    assert _harness("Save the report as output/report.md.", case, files=files) is None
    assert _harness("Save the report as output/report.md.", case) == Leak("harness-token", "SKILL.md")


@pytest.mark.ac("gh-499:AC-4")
def test_the_facade_exposes_every_harness_token_and_mask_replaces_each_occurrence() -> None:
    case = _case(request="Dry run: write output/plan.json and `output/pr-<n>.md`, then save capture/log.txt.")
    seed = _seed({"SKILL.md": "Write output/plan.json."})
    denylist = Denylist.build([case], seed=seed, phrases=("Dry run",))
    harness = denylist.harness
    assert isinstance(harness, HarnessText)
    assert set(harness.tokens) == {"output/plan.json", "output/pr-<n>.md", "capture/log.txt", "dry run"}
    assert harness == HarnessText.build([case], ("Dry run",), seed)
    masked = harness.mask("DRY RUN. See output/plan.json, output/pr-7.md, `output/pr-<n>.md`, and capture/log.txt; keep output.")
    assert masked == "[harness]. See [harness], [harness], `[harness]`, and [harness]; keep output."
    assert all(token not in repr(denylist) and token not in repr(harness) for token in harness.tokens)


@pytest.mark.ac("gh-499:AC-7")
@pytest.mark.parametrize("added", ["Write output/pr-7.md.", "Write the halt report to output/halt.md.",
                                   "Save it under capture/log.txt.", "Use `output/pr-<n>.md`."])
def test_backticked_templates_sentence_final_paths_and_capture_paths_are_derived(added: str) -> None:
    case = _case(request="Write `output/pr-<n>.md` (the plan), write the halt report to output/halt.md. "
                 + "Then save it under capture/log.txt")
    assert _harness(added, case) == Leak("harness-token", "SKILL.md")


@pytest.mark.ac("gh-499:AC-7")
@pytest.mark.parametrize("added", ["Keep the output short.", "Write output/pr-x.md.", "Read workspace/output/halt.md.",
                                   "Write output/halt.mdx."])
def test_the_bare_word_output_and_other_paths_are_not_harness_tokens(added: str) -> None:
    case = _case(request="Write `output/pr-<n>.md` and the halt report to output/halt.md. Keep the output.")
    assert _harness(added, case) is None


@pytest.mark.ac("gh-499:AC-8")
def test_a_holdout_path_is_not_a_harness_token_and_a_journey_brief_path_is() -> None:
    holdout = _case("h1", "holdout", request="write the secret to output/secret.json")
    assert _harness("Write output/secret.json.", holdout, _case()) is None
    journey = _case(journey=Journey("You run a venue. The guest wants output/plan.json.", requirements=("AGE-1",)))
    assert _harness("Write output/plan.json.", journey) == Leak("harness-token", "SKILL.md")


@pytest.mark.ac("gh-499:AC-1")
@pytest.mark.parametrize("component", ["@hypothesis", "@notes"])
def test_free_text_that_holds_a_harness_token_is_a_leak_against_an_empty_seed(component: str) -> None:
    denylist = Denylist.build([_case(request="write the plan to output/plan.json")], phrases=("do not run git or gh",))
    assert check(denylist, {}, {component: "Write the plan to output/plan.json."}) == Leak("harness-token", component)
    assert check(denylist, {}, {component: "Do not run git or gh."}) == Leak("harness-token", component)
    assert check(denylist, {}, {component: "Keep the plan short."}) is None


@pytest.mark.ac("gh-499:AC-3")
def test_a_template_held_only_in_digit_form_exempts_the_digit_pattern_but_not_the_literal_template() -> None:
    case = _case(request="write `output/pr-<n>.md` now")
    files = {**SEED, "reference.md": "An example is output/pr-12.md."}
    assert _harness("Write output/pr-7.md.", case, files=files) is None
    assert _harness("Write output/pr-<n>.md.", case, files=files) == Leak("harness-token", "SKILL.md")
    assert _harness("Write output/pr-7.md.", case) == Leak("harness-token", "SKILL.md")


@pytest.mark.ac("gh-499:AC-7")
@pytest.mark.parametrize("request_text", ["write (output/a.md), now", "write output/a.md; now", "write 'output/a.md' now",
                                          'write "output/a.md" now', "write output/a.md, now", "write [output/a.md] now"])
def test_a_request_path_ends_before_closing_punctuation(request_text: str) -> None:
    harness = Denylist.build([_case(request=request_text)]).harness
    assert harness.tokens == ("output/a.md",)
    assert _harness("Write output/a.md.", _case(request=request_text)) == Leak("harness-token", "SKILL.md")


@pytest.mark.ac("gh-499:AC-4")
def test_mask_replaces_the_longer_overlapping_token_first() -> None:
    harness = HarnessText.build([_case(request="write output/dry run.md now")], ("dry run",))
    assert harness.tokens == ("output/dry", "dry run")
    masked = harness.mask("Write output/dry run.md after a dry run of output/dry")
    assert masked == "Write [harness] run.md after a [harness] of [harness]"


@pytest.mark.ac("gh-499:AC-7")
@pytest.mark.parametrize(("request_text", "added"), [
    ("save the files under output/reports/ now", "Save them under output/reports/."),
    ("write output/plan.json—then stop", "Write output/plan.json."),
    ("write <code>output/plan.json</code> now", "Write output/plan.json."),
    ("write <output/plan.json> now", "Write output/plan.json."),
    ("write ./output/plan.json now", "Write ./output/plan.json."),
    ("write output/plan.json now", "Write ./output/plan.json.")])
def test_a_directory_path_a_tag_wrapped_path_and_a_dot_slash_path_are_harness_tokens(request_text: str, added: str) -> None:
    assert _harness(added, _case(request=request_text)) == Leak("harness-token", "SKILL.md")


@pytest.mark.ac("gh-499:AC-7")
def test_a_directory_token_does_not_match_a_longer_path_below_it() -> None:
    case = _case(request="save the files under output/reports/ now")
    assert Denylist.build([case]).harness.tokens == ("output/reports",)
    assert _harness("Read output/reports/summary.md.", case) is None


@pytest.mark.ac("gh-499:AC-3")
def test_moving_a_token_between_components_is_a_leak_unless_another_seed_file_holds_it() -> None:
    case = _case(request="write the plan to output/plan.json")
    seed = {"SKILL.md": "Review.", "notes.md": "Keep notes."}
    moved = {"SKILL.md": "Review.", "notes.md": "Keep notes. Write output/plan.json."}
    denylist = Denylist.build([case], seed=_seed(seed, "notes.md"))
    assert check(denylist, seed, moved) == Leak("harness-token", "notes.md")
    held = {"SKILL.md": "Review. Write output/plan.json.", "notes.md": "Keep notes."}
    assert check(Denylist.build([case], seed=_seed(held, "notes.md")), held, moved) is None
