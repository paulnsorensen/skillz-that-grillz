"""Property-based checks of the private argv repairs, with inputs that Hypothesis generates."""

# No `from __future__ import annotations`: Cyclopts resolves the hints of commands defined inside tests.

import shlex

from cyclopts import App, CycloptsError
from hypothesis import given, settings
from hypothesis import strategies as st

from fromargs._argv import GLOBAL_FLAGS, parse_once, repair_rejected, strip_global_flags

# Each example parses argv several times through Cyclopts, so one example can take longer than the default deadline.
PROPERTY = settings(deadline=None)

WORDS = st.from_regex(r"[a-z][a-z0-9_]{0,7}", fullmatch=True)
# Tokens that a caller can pass: the command, its options, global flags, the end-of-options marker, and merged text.
TOKENS = st.one_of(
    st.sampled_from(["show", "--count", "--label", "--tags", "--json", "--full", "--", "--help", "-h"]),
    WORDS,
    st.integers(-1000, 1000).map(str),
    st.lists(st.one_of(WORDS, st.sampled_from(["--count", "--label", "--tags", "--json", "3"])), min_size=2, max_size=4)
    .map(" ".join),
)
# Only `--count` takes a split value: `--label` and `--tags` hold free text, which a repair never splits.
OPTIONS = st.one_of(
    st.tuples(st.just("--count"), st.integers(-1000, 1000).map(str)),
    st.tuples(st.sampled_from(["--label", "--tags"]), WORDS),
    st.sampled_from([("--json",), ("--full",)]),
)


@st.composite
def _merged(draw: st.DrawFn) -> list[str]:
    """A valid argv for `show` with one run of two to four option tokens merged into one shell token.

    The merge starts after the free-text name, and no option repeats, so most examples have one verified split.
    """
    options = draw(st.lists(OPTIONS, min_size=2, max_size=4, unique_by=lambda option: option[0]))
    argv = ["show", draw(WORDS), *(token for option in options for token in option)]
    start = draw(st.integers(2, len(argv) - 2))
    stop = draw(st.integers(start + 2, min(start + 4, len(argv))))
    return argv[:start] + [" ".join(argv[start:stop])] + argv[stop:]


# Arbitrary token lists rarely need a repair, so half the examples are valid argv with merged tokens.
ARGV = st.one_of(st.lists(TOKENS, max_size=8), _merged())
# A small alphabet, so most examples hold global flags on both sides of the end-of-options marker.
FLAG_ARGV = st.lists(st.sampled_from(["show", "x", "--count", "2", "--json", "--full", "--"]), max_size=10)
# Label text that the shell can quote. A leading "-" makes Cyclopts read the value as an option.
LABELS = st.text(st.characters(codec="utf-8", exclude_categories=("Cc", "Cs")), max_size=12).filter(
    lambda text: not text.startswith("-")
)


def _app() -> App:
    app = App()

    @app.command
    def show(name: str, *, count: int = 1, tags: list[str] | None = None, label: str = "") -> None:
        raise AssertionError(f"a repair probe never runs a handler: {(name, count, tags, label)!r}")

    return app


def _parses(app: App, argv: list[str]) -> bool:
    try:
        _ = parse_once(app, strip_global_flags(app, argv)[0])
    except CycloptsError:
        return False
    return True


@PROPERTY
@given(st.one_of(FLAG_ARGV, ARGV))
def test_strip_removes_every_global_flag_before_the_marker_and_keeps_the_rest(argv: list[str]) -> None:
    app = _app()
    boundary = argv.index("--") if "--" in argv else len(argv)

    tokens, full = strip_global_flags(app, argv)

    before, after = argv[:boundary], argv[boundary:]
    assert tokens == [token for token in before if token not in GLOBAL_FLAGS] + after
    assert full == ("--full" in before)
    assert strip_global_flags(app, tokens) == (tokens, False)


@PROPERTY
@given(ARGV)
def test_a_repair_splits_exactly_one_token_and_the_result_parses(argv: list[str]) -> None:
    app = _app()

    repaired = repair_rejected(app, argv)

    if repaired is None:
        return
    assert _parses(app, repaired)
    splits = [
        index
        for index, token in enumerate(argv)
        if repaired == argv[:index] + shlex.split(token) + argv[index + 1 :]
    ]
    assert len(splits) == 1, (argv, repaired)


@PROPERTY
@given(name=WORDS, count=st.integers(), label=LABELS)
def test_a_shell_merged_option_value_is_split_back_into_the_intended_argv(name: str, count: int, label: str) -> None:
    app = _app()
    intended = ["show", name, f"--count={count}", "--label", label]
    merged = ["show", name, f"--count={count} --label {shlex.quote(label)}"]

    assert not _parses(app, merged)
    assert repair_rejected(app, merged) == intended


@PROPERTY
@given(name=WORDS, word=WORDS)
def test_a_split_that_does_not_parse_is_never_returned(name: str, word: str) -> None:
    app = _app()

    assert repair_rejected(app, ["show", name, "--count", f"{word} --label y"]) is None
