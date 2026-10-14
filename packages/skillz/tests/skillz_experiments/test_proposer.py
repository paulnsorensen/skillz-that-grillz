"""The proposer seam: the reflection request text is frozen per edit mode, and the answer check keeps its error."""
from __future__ import annotations

import pytest

from skillz_experiments.search import Progress, assess
from skillz_experiments.search import accept as accept_proposal
from skillz_experiments.search import request as reflection_request

STE = ("Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
       "active voice, present tense, one instruction per sentence, at most 20 words per procedural sentence, "
       "and at most 25 words per descriptive sentence. "
       "Return complete component contents. Do not alter independent checks or permissions.")
ASK = " Add an @hypothesis text of at most 200 characters that states why the change helps, or null. "
RULES = ("Do not re-introduce a change that broke a case. "
         + "Do not copy a case id into any component or into @hypothesis. "
         + "The case-id check rejects a component that names one and drops such a hypothesis.")
NO_ATTEMPT = ("Attempt history, newest first. "
              + "Each attempt lists its hypothesis, the train cases it fixed, and the train cases it broke. "
              + "No attempt yet. " + RULES)
STATUS = (ASK + NO_ATTEMPT + " Make at most 6 edits across all components. An edit is one block of consecutive changed lines. "
          + "Run status: this is proposal 1. Search calls spent: 0 of 100 (0%). "
          + "Proposals since the last validation gain: 0.\n")
START = Progress(spent=0, planned=100, proposal=1)
PROSE = "Improve only the supplied skill text components. Preserve the helper CLI contract. " + STE + STATUS
CLI_HEAD = ("Improve the supplied skill text and helper script components. "
             "You may change a script's command-line interface when the change helps the skill. "
             "When you change an interface, update every caller and every skill text reference to match. "
             "A non-Python helper keeps its language. "
             "Write only skill content into a component. "
             "Do not put facts about this prompt, the search, or the host into a component. "
             "The fromargs package is the default for a wedged Python source. "
             "Use one parser. "
             "Print JSON errors on stderr. "
             "Exit with code 2 for a usage error and code 3 for a failed input contract. "
             "Print only results on stdout. "
             "A Python helper may use a stdlib `argparse` helper only when the skill has no wedge.toml "
             "that can build a fromargs target. "
             "The @new-cli component adds one new fromargs wedge target as TOML with the keys name and module. "
             "The name matches [a-z][a-z0-9-]{1,39} and is new. "
             "The package is the name with each hyphen changed to an underscore. "
             "The module imports only the standard library, its own package, fromargs, and the site layer. "
             "The module defines a top-level main, and the entry is <package>:main. "
             "The host builds the target to scripts/<name>.pyz. "
             "SKILL.md calls it with python3 scripts/<name>.pyz. "
             "The host rejects a second target. ")
PROSE_CLI = CLI_HEAD + STE + STATUS


@pytest.mark.ac("gh-238:AC-1")
def test_the_prose_request_text_and_schema_are_pinned() -> None:
    prompt, schema = reflection_request("prose", {"SKILL.md": "t"}, {}, ["SKILL.md"], progress=START)
    assert prompt == PROSE + '{"candidate": {"SKILL.md": "t"}, "feedback": {}}'
    assert schema == {"type": "object",
                      "properties": {"SKILL.md": {"type": "string"}, "@hypothesis": {"type": ["string", "null"]}},
                      "required": ["SKILL.md", "@hypothesis"], "additionalProperties": False}


@pytest.mark.ac("gh-238:AC-1")
@pytest.mark.ac("gh-302:AC-1")
@pytest.mark.ac("gh-302:AC-2")
def test_the_prose_and_cli_request_text_and_schema_are_pinned() -> None:
    components = ["SKILL.md", "@new-cli"]
    prompt, schema = reflection_request("prose+cli", {name: "t" for name in components}, {}, components, progress=START)
    assert prompt == PROSE_CLI + '{"candidate": {"SKILL.md": "t", "@new-cli": "t"}, "feedback": {}}'
    assert schema == {"type": "object",
                      "properties": {"SKILL.md": {"type": "string"}, "@new-cli": {"type": "string"},
                                     "@hypothesis": {"type": ["string", "null"]}},
                      "required": [*components, "@hypothesis"], "additionalProperties": False}


@pytest.mark.ac("gh-238:AC-2")
@pytest.mark.parametrize("answer", [{"SKILL.md": "x", "extra": "y"}, {}, {"SKILL.md": 1}])
def test_an_answer_with_other_keys_or_a_non_text_value_is_refused(answer: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="reflection keys differ from the frozen components"):
        _ = accept_proposal(answer, ["SKILL.md"], {"SKILL.md": "t"})


@pytest.mark.ac("gh-238:AC-2")
def test_an_answer_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ValueError, match="expected a JSON object"):
        _ = accept_proposal(["SKILL.md"], ["SKILL.md"], {"SKILL.md": "t"})


def test_a_matching_answer_is_returned_unchanged_with_its_edit_count() -> None:
    assert accept_proposal({"SKILL.md": "x"}, ["SKILL.md"], {"SKILL.md": "t"}) == ({"SKILL.md": "x"}, 1, None, None)
    assert accept_proposal({"SKILL.md": "t"}, ["SKILL.md"], {"SKILL.md": "t"}) == ({"SKILL.md": "t"}, 0, None, None)


def _status(progress: Progress) -> str:
    prompt, _ = reflection_request("prose", {"SKILL.md": "t"}, {}, ["SKILL.md"], progress=progress)
    return prompt.split("\n", 1)[0]


@pytest.mark.ac("gh-239:AC-1")
def test_the_stated_budget_is_six_at_the_start_and_one_at_the_end() -> None:
    assert "at most 6 edits" in _status(Progress(spent=0, planned=100, proposal=1))
    assert "Make at most 1 edit across all components." in _status(Progress(spent=100, planned=100, proposal=9))
    assert "Make at most 1 edit across all components." in _status(Progress(spent=250, planned=100, proposal=9))
    assert "Make at most 2 edits across all components." in _status(Progress(spent=70, planned=100, proposal=9))


@pytest.mark.ac("gh-239:AC-1")
def test_the_budget_follows_the_cosine_schedule_rounded_down() -> None:
    steps = [Progress(spent=spent, planned=100, proposal=1).budget() for spent in range(0, 101, 10)]
    assert steps == [6, 5, 5, 4, 4, 3, 2, 2, 1, 1, 1]


@pytest.mark.ac("gh-239:AC-1")
def test_a_plan_without_calls_counts_as_spent() -> None:
    assert Progress(spent=0, planned=0, proposal=1).budget() == 1


@pytest.mark.ac("gh-239:AC-4")
def test_the_last_proposal_sentence_starts_at_85_percent() -> None:
    sentence = "This may be the last proposal, so prefer one small, safe change."
    assert sentence not in _status(Progress(spent=84, planned=100, proposal=3))
    assert sentence in _status(Progress(spent=85, planned=100, proposal=3))
    assert sentence in _status(Progress(spent=100, planned=100, proposal=3))


@pytest.mark.ac("gh-239:AC-5")
def test_the_status_block_is_pinned_for_a_late_search() -> None:
    middle = (" Make at most 2 edits across all components. An edit is one block of consecutive changed lines. "
              "Run status: this is proposal 7. Search calls spent: 72 of 120 (60%). "
              "Proposals since the last validation gain: 0.")
    last = ("Run status: this is proposal 9. Search calls spent: 110 of 120 (91%). "
            "Proposals since the last validation gain: 0. "
            "This may be the last proposal, so prefer one small, safe change.")
    assert _status(Progress(spent=72, planned=120, proposal=7)).endswith(middle)
    assert _status(Progress(spent=110, planned=120, proposal=9)).endswith(last)


def test_the_status_block_states_the_stall_count_and_ends_with_the_stall_advice() -> None:
    advice = assess([0.2] + [0.0] * 5, band=0.0).advice
    stalled = ("Search calls spent: 110 of 120 (91%). Proposals since the last validation gain: 5. "
               + "This may be the last proposal, so prefer one small, safe change. " + advice)
    assert advice and _status(Progress(spent=110, planned=120, proposal=9, stalled=5, advice=advice)).endswith(stalled)


@pytest.mark.ac("gh-239:AC-5")
@pytest.mark.parametrize(("spent", "planned", "percent"),
                         [(29, 100, 29), (57, 100, 57), (58, 100, 58), (1, 3, 33), (2, 3, 66), (7, 7, 100), (9, 7, 100)])
def test_the_percent_is_exact_integer_math_rounded_down(spent: int, planned: int, percent: int) -> None:
    assert f"({percent}%)." in _status(Progress(spent=spent, planned=planned, proposal=1))


@pytest.mark.ac("gh-239:AC-2")
def test_an_edit_is_one_block_of_changed_lines_summed_over_the_components() -> None:
    before = {"a": "one\ntwo\nthree\nfour\nfive\n", "b": "x\ny\n"}
    after = {"a": "ONE\ntwo\nthree\nFOUR\nFIVE\nsix\n", "b": "x\ny\n"}
    assert accept_proposal(after, ["a", "b"], before) == (after, 2, None, None)
    assert accept_proposal({"a": "one\ntwo\n", "b": "y\n"}, ["a", "b"], before)[1] == 2


@pytest.mark.ac("gh-239:AC-2")
def test_an_oversized_proposal_is_still_returned() -> None:
    lines = [f"line {number}" for number in range(20)]
    before = {"SKILL.md": "\n".join(lines)}
    after = {"SKILL.md": "\n".join(f"{line}\nnew" for line in lines)}
    proposal, edits, _, _ = accept_proposal(after, ["SKILL.md"], before)
    assert proposal == after and edits > Progress(spent=0, planned=100, proposal=1).budget()


def _attempt(index: int, **fields: object) -> dict[str, object]:
    base: dict[str, object] = {"index": index, "parent": "p", "candidate": f"c{index}", "components": f"d{index}", "hypothesis": f"idea {index}",
            "fixed": [], "broke": [], "validation_delta": None, "accepted": True, "rejected": None}
    return base | fields


def _section(history: list[dict[str, object]], protect: tuple[str, ...] = ()) -> str:
    prompt, _ = reflection_request("prose", {"SKILL.md": "t"}, {}, ["SKILL.md"], progress=START, history=history, protect=protect)
    return prompt.split("\n", 1)[0].split(ASK, 1)[1].split(" Make at most", 1)[0]


@pytest.mark.ac("gh-241:AC-2")
def test_the_prompt_lists_the_newest_eight_attempts_newest_first_with_hypothesis_fixed_broke_and_the_protect_set() -> None:
    history = [_attempt(number, fixed=[f"f{number}"], broke=[f"b{number}"], validation_delta=0.25) for number in range(1, 11)]
    section = _section(history, ("t1", "t2"))
    order = [section.index(f"Attempt {number}:") for number in range(10, 2, -1)]
    assert order == sorted(order) and "Attempt 2:" not in section and "Attempt 1:" not in section
    assert "Attempt 10: hypothesis idea 10; fixed f10; broke b10; validation change +0.250; result admitted." in section
    assert "Keep these train cases passing, because the current best passes them: t1, t2." in section
    assert len(section) <= 4000


@pytest.mark.ac("gh-241:AC-2")
def test_the_history_section_stays_within_4000_characters_and_keeps_the_rules() -> None:
    history = [_attempt(number, hypothesis="h" * 200, fixed=["x" * 30] * 40, rejected="r" * 300) for number in range(1, 9)]
    section = _section(history, tuple(f"case-{number:03d}" * 5 for number in range(30)))
    assert len(section) <= 4000 and section.endswith(RULES)
    assert "Attempt 8:" in section


@pytest.mark.ac("gh-241:AC-2")
def test_the_history_section_and_the_hypothesis_ask_are_pinned_and_obey_the_ste_limits() -> None:
    assert _section([]) == NO_ATTEMPT
    for sentence in (ASK + NO_ATTEMPT).split(". "):
        assert len(sentence.split()) <= 20, sentence
    assert "the train cases it fixed, and the train cases it broke" in NO_ATTEMPT
    assert RULES in NO_ATTEMPT and "Do not re-introduce a change that broke a case." in RULES
    assert "Do not copy a case id into any component or into @hypothesis." in RULES


@pytest.mark.ac("gh-241:AC-5")
def test_an_answer_without_a_hypothesis_is_accepted_with_none() -> None:
    assert accept_proposal({"SKILL.md": "x"}, ["SKILL.md"], {"SKILL.md": "t"}) == ({"SKILL.md": "x"}, 1, None, None)
    assert accept_proposal({"SKILL.md": "x", "@hypothesis": None}, ["SKILL.md"], {"SKILL.md": "t"})[2:] == (None, None)
    assert accept_proposal({"SKILL.md": "x", "@hypothesis": "it helps"}, ["SKILL.md"], {"SKILL.md": "t"})[2:] == ("it helps", None)


@pytest.mark.ac("gh-241:AC-7")
def test_the_schema_requires_a_nullable_hypothesis_without_a_length_limit_and_accept_drops_a_long_one() -> None:
    _, schema = reflection_request("prose", {"SKILL.md": "t"}, {}, ["SKILL.md"], progress=START)
    properties = schema["properties"]
    assert isinstance(properties, dict) and properties["@hypothesis"] == {"type": ["string", "null"]}
    assert schema["required"] == ["SKILL.md", "@hypothesis"]
    assert accept_proposal({"SKILL.md": "x", "@hypothesis": "h" * 200}, ["SKILL.md"], {"SKILL.md": "t"})[2:] == ("h" * 200, None)
    assert accept_proposal({"SKILL.md": "x", "@hypothesis": "h" * 201}, ["SKILL.md"], {"SKILL.md": "t"})[2:] == (None, "hypothesis-too-long")
