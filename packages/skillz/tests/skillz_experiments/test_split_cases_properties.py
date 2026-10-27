"""Property-based checks of how intake splits draft cases by family, with drafts that Hypothesis generates."""
from __future__ import annotations

import itertools
from collections.abc import Mapping
from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillz_experiments._cases import CodedError, Journey, Split
from skillz_experiments._intake import (
    HOLDOUT_MINIMUM, MINIMUM_FAMILIES, SPLITS, TRAIN_FAMILIES_MINIMUM, TRAIN_MINIMUM, DraftCase, IntakeCase, split_cases,
    too_few,
)

JOURNEY_KINDS = ("journey", "tour")
OTHER_KINDS = ("task", "trigger", "near-miss", *JOURNEY_KINDS)
FAMILY_LIMIT = 7


@dataclass(frozen=True)
class _Run:
    cases: list[DraftCase]
    seed: int
    kind: str | None
    journeys: frozenset[str]
    also: frozenset[str]

    def split(self) -> list[IntakeCase]:
        return split_cases(self.cases, self.seed, self.kind, self.journeys, self.also)


def _draft(identifier: str, family: str, kind: str) -> DraftCase:
    journey = Journey(f"brief {identifier}") if kind in JOURNEY_KINDS else None
    return DraftCase(identifier, family, kind, f"Do {identifier}", "skill", {"input.txt": identifier},
                     {"answer": identifier}, journey)


@st.composite
def _runs(draw: st.DrawFn, sized_families: int = 0) -> _Run:
    """Draw a run. The first `sized_families` families each hold at least one case that sizes the split."""
    kind = draw(st.sampled_from([None, "task", "review"]))
    journeys = draw(st.frozensets(st.sampled_from(JOURNEY_KINDS), min_size=1 if kind is None and sized_families else 0))
    also = draw(st.frozensets(st.sampled_from(JOURNEY_KINDS)))
    sized = "task" if kind is not None or not journeys else draw(st.sampled_from(sorted(journeys)))
    # A family mixes cases that size the split with pending, unscored, and `also` cases. The weight on `sized`
    # keeps the drafts that meet every minimum common in a journey run too.
    family_kinds = st.lists(st.sampled_from([sized, sized, sized, *OTHER_KINDS]), min_size=1, max_size=6)
    families = draw(st.lists(family_kinds, min_size=sized_families, max_size=FAMILY_LIMIT))
    kinds = [(f"f{index}", case_kind) for index, extra in enumerate(families)
             for case_kind in ([sized] if index < sized_families else []) + extra]
    cases = [_draft(f"c{position}", family, case_kind) for position, (family, case_kind) in enumerate(kinds)]
    return _Run(draw(st.permutations(cases)), draw(st.integers(0, 2**32 - 1)), kind, journeys, also)


def _sizes_split(case: DraftCase, run: _Run) -> bool:
    """Return whether `case` counts toward the split minimums, from the rules in the `split_cases` docstring."""
    if case.pending:
        return False
    return case.kind in run.journeys if case.journey is not None else run.kind is not None


def _sizes(run: _Run) -> dict[str, int]:
    size: dict[str, int] = {}
    for case in run.cases:
        if _sizes_split(case, run):
            size[case.family] = size.get(case.family, 0) + 1
    return size


def _feasible(size: Mapping[str, int]) -> bool:
    """Return whether any whole-family assignment meets every split minimum, by trying each one."""
    names = list(size)
    for splits in itertools.product(SPLITS, repeat=len(names)):
        train = [size[name] for name, split in zip(names, splits) if split == "train"]
        holdout = sum(size[name] for name, split in zip(names, splits) if split == "holdout")
        if (len(train) >= TRAIN_FAMILIES_MINIMUM and sum(train) >= TRAIN_MINIMUM and "validation" in splits
                and holdout >= HOLDOUT_MINIMUM):
            return True
    return False


@given(_runs(MINIMUM_FAMILIES))
def test_a_family_never_crosses_splits(run: _Run) -> None:
    result = run.split()

    split_of: dict[str, set[Split]] = {}
    for case in result:
        split_of.setdefault(case.family, set()).add(case.split)
    assert all(len(splits) == 1 for splits in split_of.values())
    assert set(split_of) == {case.family for case in run.cases}


@given(_runs(MINIMUM_FAMILIES))
def test_the_same_seed_and_input_give_the_same_output(run: _Run) -> None:
    first = run.split()

    again = split_cases(list(run.cases), run.seed, run.kind, set(run.journeys), list(run.also))

    assert again == first


@given(_runs(MINIMUM_FAMILIES))
def test_every_split_minimum_holds_whenever_some_whole_family_split_meets_it(run: _Run) -> None:
    result = run.split()

    if not _feasible(_sizes(run)):
        return
    split_of: dict[str, Split] = {case.identifier: case.split for case in result}
    totals: dict[Split, int] = dict.fromkeys(SPLITS, 0)
    families: dict[Split, set[str]] = {split: set() for split in SPLITS}
    for case in run.cases:
        if _sizes_split(case, run):
            totals[split_of[case.identifier]] += 1
            families[split_of[case.identifier]].add(case.family)
    assert totals["train"] >= TRAIN_MINIMUM
    assert len(families["train"]) >= TRAIN_FAMILIES_MINIMUM
    assert families["validation"]
    assert totals["holdout"] >= HOLDOUT_MINIMUM


@given(st.dictionaries(st.sampled_from([f"f{index}" for index in range(FAMILY_LIMIT)]), st.integers(1, 8)))
def test_too_few_is_none_exactly_when_some_whole_family_split_meets_every_minimum(size: dict[str, int]) -> None:
    stop = too_few(size)

    assert (stop is None) == _feasible(size)
    assert stop is None or stop.code == "cases-too-few"


@given(_runs())
def test_fewer_than_the_minimum_scored_families_stops_with_cases_too_few(run: _Run) -> None:
    if len(_sizes(run)) < MINIMUM_FAMILIES:
        with pytest.raises(CodedError) as caught:
            _ = run.split()
        assert caught.value.code == "cases-too-few"
    else:
        assert len(run.split()) == len(run.cases)


@given(_runs(MINIMUM_FAMILIES))
def test_no_scored_case_switches_between_a_task_kind_and_a_journey_kind(run: _Run) -> None:
    result = run.split()

    for case in result:
        if case.scored:
            assert (case.journey is not None) == (case.kind in JOURNEY_KINDS)


@given(_runs(MINIMUM_FAMILIES))
def test_scored_and_kind_follow_the_kind_journeys_and_also_rules(run: _Run) -> None:
    result = run.split()

    for draft, case in zip(run.cases, result, strict=True):
        also = draft.journey is not None and not draft.pending and draft.kind in run.also
        assert case.scored == (_sizes_split(draft, run) or also)
        retargeted = not draft.pending and draft.journey is None and run.kind is not None
        assert case.kind == (run.kind if retargeted else draft.kind)


@given(_runs(MINIMUM_FAMILIES))
def test_output_order_identifiers_and_content_match_the_input(run: _Run) -> None:
    result = run.split()

    assert [case.identifier for case in result] == [case.identifier for case in run.cases]
    assert [(case.family, case.request, case.source, case.files, case.expected, case.journey) for case in result] == [
        (case.family, case.request, case.source, case.files, case.expected, case.journey) for case in run.cases]
