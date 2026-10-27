"""Property-based checks of how the proposer accepts a reflection answer, with inputs that Hypothesis generates."""
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillz_experiments.search import HYPOTHESIS, NOTES
from skillz_experiments.search import accept as accept_proposal
from skillz_experiments.search._proposer import HYPOTHESIS_NOT_TEXT, HYPOTHESIS_TOO_LONG, MAX_HYPOTHESIS

REFUSED = "reflection keys differ from the frozen components"
NAMES = st.from_regex(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,11}", fullmatch=True)
COMPONENTS = st.lists(NAMES, min_size=1, max_size=4, unique=True)
# Text with line endings of every kind, so the line diff sees changed, added, and removed lines.
TEXT = st.lists(st.sampled_from(["a", "b", "", " ", "\n", "\r\n", "\r", "x y"]), max_size=8).map("".join) | st.text(max_size=20)
NOT_TEXT = st.one_of(st.integers(), st.booleans(), st.floats(allow_nan=False), st.lists(st.integers(), max_size=2),
                     st.dictionaries(st.text(max_size=3), st.integers(), max_size=2))


@st.composite
def _answers(draw: st.DrawFn) -> tuple[list[str], dict[str, str], dict[str, str]]:
    components = draw(COMPONENTS)
    answer = {name: draw(TEXT) for name in components}
    candidate = {name: draw(st.sampled_from([answer[name], draw(TEXT)])) for name in components
                 if draw(st.booleans())}
    return components, answer, candidate


@given(_answers())
def test_an_answer_with_exactly_the_components_is_returned_with_no_edits_only_when_unchanged(
        case: tuple[list[str], dict[str, str], dict[str, str]]) -> None:
    components, answer, candidate = case

    texts, edits, hypothesis, dropped = accept_proposal(answer, components, candidate)

    assert texts == answer
    assert (hypothesis, dropped) == (None, None)
    assert edits >= 0
    assert (edits == 0) == all(candidate.get(name, "") == text for name, text in answer.items())


@given(_answers())
def test_the_edit_count_is_the_sum_of_the_edit_counts_of_each_component(
        case: tuple[list[str], dict[str, str], dict[str, str]]) -> None:
    components, answer, candidate = case

    _, total, _, _ = accept_proposal(answer, components, candidate)

    assert total == sum(accept_proposal({name: answer[name]}, [name], candidate)[1] for name in components)


@given(_answers(), NAMES)
def test_an_answer_with_another_key_is_refused(case: tuple[list[str], dict[str, str], dict[str, str]], extra: str) -> None:
    components, answer, candidate = case
    if extra in components:
        with pytest.raises(ValueError, match=REFUSED):
            _ = accept_proposal({name: text for name, text in answer.items() if name != extra}, components, candidate)
    else:
        with pytest.raises(ValueError, match=REFUSED):
            _ = accept_proposal(answer | {extra: "x"}, components, candidate)


@given(_answers(), st.data())
def test_an_answer_with_a_value_that_is_not_text_is_refused(
        case: tuple[list[str], dict[str, str], dict[str, str]], data: st.DataObject) -> None:
    components, answer, candidate = case
    name = data.draw(st.sampled_from(components))

    with pytest.raises(ValueError, match=REFUSED):
        _ = accept_proposal(answer | {name: data.draw(NOT_TEXT)}, components, candidate)


@given(_answers(), st.text(), st.one_of(st.none(), st.text(), NOT_TEXT))
def test_a_text_hypothesis_is_kept_stripped_unless_blank_or_too_long_and_notes_never_change_the_result(
        case: tuple[list[str], dict[str, str], dict[str, str]], said: str, notes: object) -> None:
    components, answer, candidate = case
    stripped = said.strip()
    expected = ((None, HYPOTHESIS_TOO_LONG) if len(stripped) > MAX_HYPOTHESIS
                else (stripped or None, None))

    texts, edits, hypothesis, dropped = accept_proposal(answer | {HYPOTHESIS: said, NOTES: notes}, components, candidate)

    assert (hypothesis, dropped) == expected
    assert (texts, edits) == accept_proposal(answer, components, candidate)[:2]


@given(_answers(), NOT_TEXT)
def test_a_hypothesis_that_is_not_text_is_dropped_and_the_answer_is_kept(
        case: tuple[list[str], dict[str, str], dict[str, str]], said: object) -> None:
    components, answer, candidate = case

    texts, _, hypothesis, dropped = accept_proposal(answer | {HYPOTHESIS: said}, components, candidate)

    assert texts == answer
    assert (hypothesis, dropped) == (None, HYPOTHESIS_NOT_TEXT)
