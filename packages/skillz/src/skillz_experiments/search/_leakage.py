"""Deterministic leakage check: a proposal may not copy case content into the skill.

The denylist holds train and validation cases only; the proposer never sees holdout. The check looks at text a
proposal adds to a component, so content the seed already holds never triggers.
"""
from __future__ import annotations

import re
import string
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import PurePosixPath
from typing import cast

from skillz_experiments._cases import Case

SHINGLE_WORDS = 8
MIN_ANSWER_CHARS = 20


@dataclass(frozen=True)
class Leak:
    """The rule that fired and the component it fired on. It never carries the matched text."""

    rule: str
    component: str


def _words(text: str) -> list[str]:
    return [word for word in (raw.strip(string.punctuation) for raw in text.lower().split()) if word]


def _shingles(text: str) -> frozenset[tuple[str, ...]]:
    words = _words(text)
    return frozenset(tuple(words[start:start + SHINGLE_WORDS]) for start in range(len(words) - SHINGLE_WORDS + 1))


def _squash(text: str) -> str:
    return " ".join(text.lower().split())


def _leaves(value: object) -> Iterator[str]:
    """Yield every string leaf of a string, mapping, list, or dataclass such as `Audit`."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in cast(Mapping[object, object], value).values():
            yield from _leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in cast(Sequence[object], value):
            yield from _leaves(item)
    elif is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            yield from _leaves(cast(object, getattr(value, item.name)))


def _token(name: str) -> re.Pattern[str]:
    """Match `name` as a whole token, so it never matches inside a longer word or hyphenated name."""
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.IGNORECASE)


@dataclass(frozen=True)
class Denylist:
    """Case content a proposal must not copy. The fields stay out of `repr`, so a log never prints case content."""

    spans: frozenset[tuple[str, ...]] = field(repr=False)
    answers: frozenset[str] = field(repr=False)
    identifiers: tuple[re.Pattern[str], ...] = field(repr=False)
    fixtures: tuple[re.Pattern[str], ...] = field(repr=False)

    @classmethod
    def build(cls, cases: Iterable[Case]) -> Denylist:
        """Build the denylist from the train and validation cases. Holdout cases are skipped."""
        spans: set[tuple[str, ...]] = set()
        answers: set[str] = set()
        identifiers: set[str] = set()
        fixtures: set[str] = set()
        for case in cases:
            if case.split == "holdout":
                continue
            spans |= _shingles(case.request)
            if case.journey is not None:
                spans |= _shingles(case.journey.brief)
            answers |= {leaf for leaf in map(_squash, _leaves(case.expected)) if len(leaf) >= MIN_ANSWER_CHARS}
            identifiers.add(case.identifier)
            fixtures |= {PurePosixPath(name).name for name in case.files}
        return cls(frozenset(spans), frozenset(answers),
                   tuple(_token(name) for name in sorted(identifiers)), tuple(_token(name) for name in sorted(fixtures)))


def _rule(denylist: Denylist, before: str, after: str) -> str | None:
    if (_shingles(after) - _shingles(before)) & denylist.spans:
        return "request-span"
    squashed_before, squashed_after = _squash(before), _squash(after)
    if any(answer in squashed_after and answer not in squashed_before for answer in denylist.answers):
        return "expected-answer"
    for rule, patterns in (("case-id", denylist.identifiers), ("fixture-name", denylist.fixtures)):
        if any(pattern.search(after) and not pattern.search(before) for pattern in patterns):
            return rule
    return None


def check(denylist: Denylist, seed: Mapping[str, str], proposal: Mapping[str, str]) -> Leak | None:
    """Return the first leak in the text that `proposal` adds to `seed`, else None. Components run in name order."""
    for component in sorted(proposal):
        before = seed.get(component, "")
        if proposal[component] == before:
            continue
        rule = _rule(denylist, before, proposal[component])
        if rule is not None:
            return Leak(rule, component)
    return None
