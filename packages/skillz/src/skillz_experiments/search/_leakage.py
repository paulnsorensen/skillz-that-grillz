"""Deterministic leakage check: a proposal may not copy case content or runner context into the skill.

The denylist holds train and validation cases only; the proposer never sees holdout. The check looks at text a
proposal adds to a component, so content the seed already holds never triggers. Harness text, the sandbox paths of the
requests and the phrases that the contract declares, is denied too.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import PurePosixPath
from typing import cast

from skillz_experiments._candidate import NEW_CLI, WEDGE_PREFIX, Candidate
from skillz_experiments._cases import Case
from skillz_experiments._graders import CAPTURE_DIRECTORY, OUTPUT_DIRECTORY

SHINGLE_WORDS = 8
MIN_ANSWER_CHARS = 20
# Unicode punctuation and symbols at a word edge, so curly quotes strip like ASCII quotes.
_EDGES = re.compile(r"^[\W_]+|[\W_]+$")
# A fixture name followed by `.` and a word character is a longer file name such as `ledger.csv.bak`.
_FIXTURE_TAIL = r"[\w-]|\.[\w-]"
# Runner and wedge terms. A target that offers no wedge source and no `@new-cli` gets no wedge text in its prompt (gh-302).
RUNNER_TERMS = ("wedge.toml", "fromargs", NEW_CLI)
HARNESS_MASK = "[harness]"
# Path characters: word characters, `.~+@%=-`, a `<...>` placeholder, and `/`. A path ends at any other character, such as
# whitespace, a quote, a backtick, a bracket, a comma, a semicolon, an em dash, or an HTML tag.
_PATH_CHARS = r"(?:[\w.~+@%=-]|<[^<>\s/]+>|/)"
# A token starts after a character that cannot be part of a path, and may carry a `./` prefix.
_START = r"(?:(?<![\w./-])\./|(?<![\w./-]))"
# A sandbox path in a request: `output/` or `capture/`, then path characters.
_HARNESS_PATH = re.compile(rf"{_START}(?:{OUTPUT_DIRECTORY}|{CAPTURE_DIRECTORY})/{_PATH_CHARS}+", re.IGNORECASE)
# A token ends before a character that would continue it. A trailing `/` ends a directory path.
_END = r"(?![\w-]|/[\w<~+@%=-]|/?\.[\w-])"
# A template placeholder in a path segment, such as `<n>` in `pr-<n>.md`. It matches one or more digits.
_PLACEHOLDER = re.compile(r"<[^<>\s/]+>")
# Surrounding punctuation of a harness token. A placeholder bracket stays.
_SURROUNDING = re.compile(r"^[^\w<>]+|[^\w<>]+$")


def _runner_terms(seed: Candidate | None) -> tuple[re.Pattern[str], ...]:
    """Return the runner terms that are runner context for `seed`.

    A seed that offers a wedge source or `@new-cli` gets wedge text in its prompt, so no term is runner context.
    A term that any seed file holds is target vocabulary, so it is not runner context either.
    """
    if seed is None or any(name == NEW_CLI or name.startswith(WEDGE_PREFIX) for name in seed.editable):
        return ()
    patterns = (_token(term) for term in RUNNER_TERMS)
    return tuple(pattern for pattern in patterns if not any(pattern.search(text) for text in seed.files.values()))


@dataclass(frozen=True)
class Leak:
    """The rule that fired and the component it fired on. It never carries the matched text."""

    rule: str
    component: str


def _words(text: str) -> list[str]:
    return [word for word in (_EDGES.sub("", raw) for raw in text.lower().split()) if word]


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


def _token(name: str, tail: str = r"[\w-]") -> re.Pattern[str]:
    """Match `name` as a whole token, so it never matches inside a longer word or hyphenated name."""
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?!{tail})", re.IGNORECASE)


def _harness_token(text: str) -> str:
    """Normalize a harness token: lower-cased, whitespace collapsed, surrounding punctuation stripped."""
    return _SURROUNDING.sub("", " ".join(text.lower().split()))


def _harness_patterns(token: str) -> tuple[re.Pattern[str], ...]:
    """Return the patterns of one normalized token. Each whitespace run matches any whitespace, and case is ignored.

    A path template gives a second pattern in which each placeholder matches one or more digits.
    """
    bodies = [r"\s+".join(map(re.escape, token.split()))]
    if _PLACEHOLDER.search(token):
        bodies.append(r"\d+".join(map(re.escape, _PLACEHOLDER.split(token))))
    return tuple(re.compile(f"{_START}{body}{_END}", re.IGNORECASE) for body in bodies)


@dataclass(frozen=True)
class HarnessText:
    """Harness text that the eval cases and the contract hold: sandbox paths and declared phrases.

    `tokens` and `patterns` hold every token, seed-held ones included; `mask` uses them. `rejected` drops each pattern that a
    seed file holds, because that text is target vocabulary. The fields stay out of `repr`, so a log never prints case text.
    """

    tokens: tuple[str, ...] = field(default=(), repr=False)
    patterns: tuple[re.Pattern[str], ...] = field(default=(), repr=False)
    rejected: tuple[re.Pattern[str], ...] = field(default=(), repr=False)

    @classmethod
    def build(cls, cases: Iterable[Case], phrases: Iterable[str] = (), seed: Candidate | None = None) -> HarnessText:
        """Build the list from the train and validation requests and journey briefs, and from the declared phrases.

        Holdout cases are skipped. A request path whose first segment is `output` or `capture` is a token.
        """
        found: set[str] = {_harness_token(phrase) for phrase in phrases}
        for case in cases:
            if case.split == "holdout":
                continue
            texts = (case.request,) if case.journey is None else (case.request, case.journey.brief)
            for text in texts:
                found |= {token for token in map(_harness_token, _HARNESS_PATH.findall(text)) if _HARNESS_PATH.fullmatch(token)}
        tokens = tuple(sorted((token for token in found if token), key=lambda token: (-len(token), token)))
        patterns = tuple(pattern for token in tokens for pattern in _harness_patterns(token))
        held = () if seed is None else tuple(seed.files.values())
        return cls(tokens, patterns, tuple(pattern for pattern in patterns if not any(pattern.search(text) for text in held)))

    def mask(self, text: str) -> str:
        """Replace each harness token in `text` with `[harness]`. Longer tokens go first."""
        for pattern in self.patterns:
            text = pattern.sub(HARNESS_MASK, text)
        return text


@dataclass(frozen=True)
class Denylist:
    """Case content a proposal must not copy. The fields stay out of `repr`, so a log never prints case content."""

    spans: frozenset[tuple[str, ...]] = field(repr=False)
    answers: frozenset[str] = field(repr=False)
    identifiers: tuple[re.Pattern[str], ...] = field(repr=False)
    fixtures: tuple[re.Pattern[str], ...] = field(repr=False)
    runner_terms: tuple[re.Pattern[str], ...] = field(default=(), repr=False)
    harness: HarnessText = field(default_factory=HarnessText, repr=False)

    @classmethod
    def build(cls, cases: Iterable[Case], *, seed: Candidate | None = None, phrases: Iterable[str] = ()) -> Denylist:
        """Build the denylist from the train and validation cases. Holdout cases are skipped.

        With `seed`, the runner terms that are runner context for that seed are denied too. A fixture file name that
        a seed file already holds is target vocabulary, as a runner term is, so the denylist drops it. `phrases` are the
        harness phrases that the contract declares; `harness` holds them with the request paths.
        """
        cases = tuple(cases)
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
        held = () if seed is None else tuple(seed.files.values())
        named = (_token(name, _FIXTURE_TAIL) for name in sorted(fixtures))
        return cls(frozenset(spans), frozenset(answers), tuple(_token(name) for name in sorted(identifiers)),
                   tuple(pattern for pattern in named if not any(pattern.search(text) for text in held)),
                   _runner_terms(seed), HarnessText.build(cases, phrases, seed))


def _rule(denylist: Denylist, before: str, after: str) -> str | None:
    if (_shingles(after) - _shingles(before)) & denylist.spans:
        return "request-span"
    squashed_before, squashed_after = _squash(before), _squash(after)
    if any(answer in squashed_after and answer not in squashed_before for answer in denylist.answers):
        return "expected-answer"
    for rule, patterns in (("case-id", denylist.identifiers), ("fixture-name", denylist.fixtures),
                           ("runner-term", denylist.runner_terms), ("harness-token", denylist.harness.rejected)):
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
