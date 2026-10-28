"""Property-based checks of ``parse_requirements``, with ``uv export`` documents that Hypothesis generates."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wedge._guard import GuardError
from wedge._resolve import parse_requirements

# The autouse layer-cache fixture of conftest.py is function-scoped, but no example writes a file.
PROPERTY = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

Requirement = tuple[str, str, str | None]

NAMES = st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,12}", fullmatch=True)
VERSIONS = st.from_regex(r"[A-Za-z0-9.+!_-]{1,12}", fullmatch=True)
# Printable ASCII without a backslash, and no space at either end: a marker as `uv export` writes it.
MARKERS = st.from_regex(r"[!-\[\]-~]([ -\[\]-~]{0,20}[!-\[\]-~])?", fullmatch=True)
SPACE = st.text(st.sampled_from(" \t"), max_size=3)
# Free text on one line: str.splitlines breaks on every Cc, Zl, and Zp character.
LINE_TEXT = st.text(st.characters(exclude_categories=("Cc", "Cs", "Zl", "Zp")), max_size=20)
# Lines that parse_requirements skips: blank, comment, and hash lines, each with any indentation.
SKIPPED = st.one_of(
    SPACE,
    st.builds("{}#{}".format, SPACE, LINE_TEXT),
    st.builds("{}--hash=sha256:{}".format, SPACE, st.from_regex(r"[0-9a-f]{64}", fullmatch=True)),
)
SEPARATORS = st.sampled_from(["\n", "\r\n"])


@st.composite
def _requirement(draw: st.DrawFn) -> tuple[Requirement, str]:
    """One pinned requirement and its rendered line, with optional marker, spacing, and continuation."""
    name, version, marker = draw(NAMES), draw(VERSIONS), draw(st.none() | MARKERS)
    line = f"{name}=={version}"
    if marker is not None:
        line += f"{draw(SPACE)};{draw(SPACE)}{marker}"
    line += draw(st.sampled_from(["", " \\", "\\"]))
    return (name, version, marker), line


@st.composite
def _document(draw: st.DrawFn) -> tuple[list[Requirement], list[str]]:
    """Requirement lines in order, with skipped lines interleaved before and after each one."""
    requirements = draw(st.lists(_requirement(), max_size=6))
    lines: list[str] = draw(st.lists(SKIPPED, max_size=2))
    for _requirement_tuple, line in requirements:
        lines.append(line)
        lines.extend(draw(st.lists(SKIPPED, max_size=2)))
    return [parsed for parsed, _line in requirements], lines


# Requirement lines that are not `name==version[; marker]`: editable, path, URL, and unpinned forms.
REJECTED = st.one_of(
    st.builds("-e ./{}".format, NAMES),
    st.builds("{}{}".format, st.sampled_from(["./", "../", "/"]), NAMES),
    st.builds("{} @ file:///{}".format, NAMES, NAMES),
    st.builds("https://example.com/{}-{}.whl".format, NAMES, VERSIONS),
    st.builds("{0} @ https://example.com/{0}-{1}.whl".format, NAMES, VERSIONS),
    NAMES,
    st.builds(
        "{}{}{}".format,
        NAMES,
        st.sampled_from([">=", "<=", "~=", "!=", "<", ">", "===", "=="]),
        st.one_of(st.just(""), st.from_regex(r"[=<>~][0-9.]{1,5}", fullmatch=True)),
    ),
    st.builds("{}[extra]=={}".format, NAMES, VERSIONS),
)


@PROPERTY
@given(_document(), SEPARATORS)
def test_rendered_requirements_parse_back_in_order(document: tuple[list[Requirement], list[str]], separator: str) -> None:
    expected, lines = document

    assert parse_requirements(separator.join(lines)) == expected


@PROPERTY
@given(_document(), REJECTED, st.data())
def test_any_other_requirement_line_is_a_guard_error(
    document: tuple[list[Requirement], list[str]], rejected: str, data: st.DataObject
) -> None:
    _expected, lines = document
    position = data.draw(st.integers(0, len(lines)))
    padded = data.draw(SPACE) + rejected + data.draw(SPACE)
    lines.insert(position, padded)

    with pytest.raises(GuardError) as raised:
        _ = parse_requirements("\n".join(lines))

    assert repr(rejected) in str(raised.value)


@PROPERTY
@given(_document(), st.data())
def test_whitespace_around_a_line_is_ignored(document: tuple[list[Requirement], list[str]], data: st.DataObject) -> None:
    expected, lines = document
    padded = [data.draw(SPACE) + line + data.draw(SPACE) for line in lines]

    assert parse_requirements("\n".join(padded)) == parse_requirements("\n".join(lines)) == expected
