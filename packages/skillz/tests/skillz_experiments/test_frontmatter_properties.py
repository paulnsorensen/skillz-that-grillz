"""Property-based checks of the frontmatter readers, with inputs that Hypothesis generates.

`read_frontmatter` is the shared reader. `_facts._frontmatter` reads the same block for the rubric checks, and
`_settings._frontmatter_name` reads its `name:` field. The locked workspace environment holds PyYAML, so PyYAML is
the reference on the YAML subset that the readers claim.
"""
from __future__ import annotations

from typing import cast

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from skillz_experiments._facts import _frontmatter as facts_frontmatter  # pyright: ignore[reportPrivateUsage]
from skillz_experiments._frontmatter import read_frontmatter
from skillz_experiments.harness._claude._settings import _frontmatter_name  # pyright: ignore[reportPrivateUsage]

# No character that `str.splitlines` breaks on, so each drawn string stays on one line. The text mixes Unicode runs
# with the characters the readers treat specially, which plain Unicode text seldom holds.
ONE_LINE = ("Cc", "Cs", "Zl", "Zp")
SPECIAL = " \t:#'\"\\a-"


def _texts(excluded: str) -> st.SearchStrategy[str]:
    chunks = (st.text(st.characters(exclude_categories=ONE_LINE, exclude_characters=excluded), max_size=3)
              | st.sampled_from([char for char in SPECIAL if char not in excluded]))
    return st.lists(chunks, max_size=5).map("".join)


LINE = _texts("")
KEYS = st.from_regex(r"[A-Za-z_][A-Za-z0-9_-]{0,11}", fullmatch=True)
SEPARATORS = st.sampled_from(["", " ", "  ", "\t"])
# A plain value has no `#`, so only the drawn comment is a comment, and it does not open a quote, a collection,
# or a block scalar.
PLAIN = _texts("#").map(str.strip).filter(lambda value: value[:1] not in ("'", '"', "[", "{", "|", ">"))


def _header(indicator: str, chomping: str, indentation: str, indentation_first: bool) -> str:
    return indicator + (indentation + chomping if indentation_first else chomping + indentation)


# A block scalar header: `|` or `>`, then an optional chomping indicator and indentation digit in either order.
HEADERS = st.builds(_header, st.sampled_from("|>"), st.sampled_from(["", "-", "+"]), st.sampled_from(["", "1", "4", "9"]),
                    st.booleans())
INDENTS = st.sampled_from([" ", "  ", "    ", "\t"])
CHILDREN = st.lists(st.sampled_from(["  sub: x", "  - item", "- item", "    deeper: [1, 2]", "  sub: {a: b}", "  # note"]),
                    min_size=1, max_size=4)
NOT_KEYS = st.text(st.characters(exclude_categories=ONE_LINE, exclude_characters=":"), min_size=1, max_size=12).filter(
    lambda line: not line[0].isspace() and line[0] != "#" and not line.startswith(("- ", "? ")) and line != "---")
# Text that leans on the characters the reader treats specially, so arbitrary input reaches its branches.
YAMLISH = st.text(st.sampled_from(list("ab :#'\"\\|>-+[]{}?\n\t1")), max_size=40)
# A block of field-shaped lines whose values may hold broken quotes, open collections, and block headers.
FIELDISH = st.lists(st.text(st.sampled_from(list("ab :#'\"\\|>-[]{}")), max_size=10).map("k: {}".format) | YAMLISH,
                    max_size=5).map("\n".join).map("---\n{}\n---\n".format)
# A tail that often holds another `---` line and another field.
TAILS = st.text() | st.lists(LINE | st.sampled_from(["---", "name: other", "- x", ""]), max_size=4).map("\n".join)
ASCII = st.characters(min_codepoint=0x20, max_codepoint=0x7E)
# PyYAML reads these keys and plain values as strings: no bool, null, or number starts with `k` or is this long.
YAML_KEYS = st.from_regex(r"k[a-z0-9_]{0,8}", fullmatch=True)
YAML_PLAIN = st.from_regex(r"[a-z][a-z0-9 ./_()-]{5,15}[a-z0-9]", fullmatch=True)
YAML_BODY = st.lists(st.from_regex(r"[!-~](?:[ -~]{0,10}[!-~])?", fullmatch=True) | st.just(""), max_size=4)


@st.composite
def _comments(draw: st.DrawFn) -> str:
    """Draw no comment, or a comment after whitespace whose text may hold quotes, colons, and more `#`."""
    if draw(st.booleans()):
        return ""
    return draw(st.sampled_from([" ", "  ", "\t"])) + "#" + draw(LINE)


@st.composite
def _scalars(draw: st.DrawFn) -> tuple[str, str]:
    """Draw a rendered one-line value, plain or quoted, with an optional comment, and the value it must read as."""
    quote = draw(st.sampled_from(["", '"', "'"]))
    if not quote:
        value = draw(PLAIN)
    else:
        value = draw(_texts(quote + "\\"))
    return quote + value + quote + draw(_comments()), value


@st.composite
def _documents(draw: st.DrawFn) -> tuple[str, dict[str, str]]:
    """Draw a frontmatter block of one-line fields, with blank and comment lines, and the fields it must read as."""
    fields = draw(st.dictionaries(st.just("name") | KEYS, _scalars(), max_size=5))
    lines = ["---"]
    for key, (rendered, _) in fields.items():
        lines += draw(st.lists(st.sampled_from(["", "   ", "# note", "#: not a key"]), max_size=1))
        lines.append(f"{key}:{draw(SEPARATORS)}{rendered}")
    return "\n".join([*lines, "---", "Body.\n"]), {key: value for key, (_, value) in fields.items()}


@st.composite
def _yaml_fields(draw: st.DrawFn) -> tuple[str, frozenset[str]]:
    """Draw a YAML block that PyYAML reads as string, empty, nested, and literal block fields; name the block keys."""
    lines: list[str] = []
    literal: set[str] = set()
    for key in draw(st.lists(YAML_KEYS, unique=True, max_size=5)):
        kind = draw(st.sampled_from(["plain", '"', "'", "empty", "nested", "literal"]))
        comment = draw(st.just("") | st.text(ASCII, max_size=8).map("#".__add__).map(" ".__add__))
        if kind == "plain":
            lines.append(f"{key}: {draw(YAML_PLAIN)}{comment}")
        elif kind in ('"', "'"):
            text = draw(st.text(st.characters(min_codepoint=0x20, max_codepoint=0x7E, exclude_characters=kind + "\\"),
                                max_size=10))
            lines.append(f"{key}: {kind}{text}{kind}{comment}")
        elif kind == "empty":
            lines.append(f"{key}:{comment}")
        elif kind == "nested":
            lines += [f"{key}:{comment}", draw(st.sampled_from(["  sub: x", "  - item", "- item"]))]
        else:
            literal.add(key)
            header = draw(st.sampled_from(["|", "|-", "|+"]))
            lines += [f"{key}: {header}{comment}", *(f"  {row}" if row else "" for row in draw(YAML_BODY))]
    return "\n".join(lines), frozenset(literal)


@st.composite
def _shared_fields(draw: st.DrawFn) -> tuple[list[str], frozenset[str]]:
    """Draw block lines in the overlap of both readers: one-line fields and `|` or `|-` blocks; name the block keys."""
    lines: list[str] = []
    literal: set[str] = set()
    for key in draw(st.lists(KEYS, unique=True, max_size=5)):
        if draw(st.booleans()):
            lines.append(f"{key}:{draw(SEPARATORS)}{draw(_scalars())[0]}")
        else:
            literal.add(key)
            rows = draw(st.lists(LINE.map(str.strip), max_size=4))
            lines += [f"{key}: {draw(st.sampled_from(['|', '|-']))}", *(f"  {row}" if row else "" for row in rows)]
    return lines, frozenset(literal)


def _outcome(text: str) -> dict[str, str] | str:
    try:
        return read_frontmatter(text)
    except ValueError as error:
        return f"ValueError: {error}"


@given(_documents())
def test_one_line_fields_read_back_without_quotes_or_comments(document: tuple[str, dict[str, str]]) -> None:
    text, fields = document

    assert read_frontmatter(text) == fields


@given(KEYS.filter(lambda key: key != "after"), HEADERS, _comments(), st.lists(st.tuples(INDENTS, LINE), max_size=4),
       _scalars())
def test_a_block_scalar_reads_as_its_stripped_body_and_ends_at_the_next_key(
        key: str, header: str, comment: str, body: list[tuple[str, str]], after: tuple[str, str]) -> None:
    text = "\n".join(["---", f"{key}: {header}{comment}", *(indent + row for indent, row in body), f"after: {after[0]}",
                      "---"])

    assert read_frontmatter(text) == {key: "\n".join(row.strip() for _, row in body).strip(), "after": after[1]}


@given(KEYS.filter(lambda key: key != "after"), _comments(), CHILDREN, _scalars())
def test_a_nested_mapping_or_list_reads_as_an_empty_value(
        key: str, comment: str, children: list[str], after: tuple[str, str]) -> None:
    text = "\n".join(["---", f"{key}:{comment}", *children, f"after: {after[0]}", "---"])

    assert read_frontmatter(text) == {key: "", "after": after[1]}


@given(st.booleans(), LINE.filter(lambda line: line != "---"),
       st.lists(LINE | st.sampled_from(["--- ", " ---", "----", "name: x"]), max_size=5))
def test_text_without_an_opening_and_a_closing_delimiter_is_refused(opened: bool, first: str, rest: list[str]) -> None:
    lines = ["---", *(line for line in rest if line != "---")] if opened else [first, *rest, "---"]

    with pytest.raises(ValueError, match="delimiters"):
        _ = read_frontmatter("\n".join(lines))


@given(_documents(), NOT_KEYS, st.data())
def test_a_top_level_line_that_is_not_a_key_is_refused(
        document: tuple[str, dict[str, str]], line: str, data: st.DataObject) -> None:
    lines = document[0].split("\n")
    lines.insert(data.draw(st.integers(1, lines.index("---", 1))), line)

    with pytest.raises(ValueError, match="not a key"):
        _ = read_frontmatter("\n".join(lines))


@given(st.text() | YAMLISH.map("---\n{}\n---\n".format) | FIELDISH)
def test_any_text_reads_or_raises_only_value_error(text: str) -> None:
    try:
        _ = read_frontmatter(text)
    except ValueError:
        pass


@given(YAMLISH, TAILS)
def test_text_after_the_closing_delimiter_never_changes_the_result(body: str, tail: str) -> None:
    front = f"---\n{body}\n---\n"

    assert _outcome(front + tail) == _outcome(front)


@given(_yaml_fields())
def test_the_reader_agrees_with_pyyaml_on_flat_nested_and_literal_fields(case: tuple[str, frozenset[str]]) -> None:
    block, literal = case
    loaded = cast("dict[str, object] | None", yaml.safe_load(block)) or {}
    expected = {key: (value.strip() if key in literal else value) if isinstance(value, str) else ""
                for key, value in loaded.items()}

    assert read_frontmatter(f"---\n{block}\n---\n") == expected


@given(_shared_fields())
def test_the_audit_reader_agrees_with_the_reader_on_one_line_fields_and_literal_blocks(
        case: tuple[list[str], frozenset[str]]) -> None:
    rows, literal = case
    lines = ["---", *rows, "---"]
    fields = facts_frontmatter(lines, len(lines) - 1)

    assert {key: field.text().strip() if key in literal else field.text() for key, field in fields.items()} == \
        read_frontmatter("\n".join(lines))


@given(_documents(), st.none() | NOT_KEYS)
def test_the_frontmatter_name_is_the_nonempty_name_field_or_none(
        document: tuple[str, dict[str, str]], broken: str | None) -> None:
    text, fields = document
    if broken is not None:
        text = text.replace("\n---\n", f"\n{broken}\n---\n", 1)

    assert _frontmatter_name(text) == (None if broken is not None else fields.get("name") or None)


@pytest.mark.xfail(strict=True, reason="read_frontmatter keeps only the first line of a multi-line plain value")
@given(KEYS, st.from_regex(r"[a-z]{1,8}", fullmatch=True), st.lists(st.from_regex(r"[a-z]{1,8}", fullmatch=True), min_size=1,
                                                                   max_size=3))
def test_a_plain_value_that_continues_on_indented_lines_is_folded_or_refused(key: str, first: str, rest: list[str]) -> None:
    text = "\n".join(["---", f"{key}: {first}", *(f"  {word}" for word in rest), "---"])
    try:
        fields = read_frontmatter(text)
    except ValueError:
        return

    assert fields[key] == " ".join([first, *rest])
