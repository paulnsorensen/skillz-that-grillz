"""Property-based checks of ``content_sha256``, with archives that Hypothesis generates."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wedge._digest import content_sha256

# Each example writes its archives to its own temporary directory. The autouse layer-cache fixture of
# conftest.py is function-scoped, and no example uses it.
PROPERTY = settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])

Member = tuple[str, bytes]

MEMBER = st.tuples(st.from_regex(r"[a-zé_]{1,6}(/[a-z]{1,6})?(\.py)?", fullmatch=True), st.binary(max_size=64))
MEMBERS = st.lists(MEMBER, max_size=5, unique_by=lambda member: member[0])


@dataclass(frozen=True)
class Encoding:
    """How an archive stores its members: everything the content digest must ignore."""

    compression: int
    level: int
    date_time: tuple[int, int, int, int, int, int]
    mode: int
    shebang: bytes
    comment: bytes


ENCODINGS = st.builds(
    Encoding,
    compression=st.sampled_from([ZIP_STORED, ZIP_DEFLATED]),
    level=st.integers(0, 9),
    date_time=st.tuples(
        st.integers(1980, 2107), st.integers(1, 12), st.integers(1, 28), st.integers(0, 23), st.integers(0, 59), st.integers(0, 58)
    ),
    mode=st.sampled_from([0o644, 0o755, 0o600]),
    shebang=st.sampled_from([b"", b"#!/usr/bin/env python3\n"]),
    comment=st.binary(max_size=16),
)


def _digest(members: list[Member], encoding: Encoding) -> str:
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "skill.pyz"
        with path.open("wb") as output:
            _ = output.write(encoding.shebang)
            with ZipFile(output, "w") as archive:
                archive.comment = encoding.comment
                for name, data in members:
                    info = ZipInfo(name, date_time=encoding.date_time)
                    info.compress_type = encoding.compression
                    info.external_attr = encoding.mode << 16
                    archive.writestr(info, data, compresslevel=encoding.level)
        first = content_sha256(path)
        assert content_sha256(path) == first
        return first


@PROPERTY
@given(MEMBERS, ENCODINGS, ENCODINGS)
def test_the_digest_ignores_compression_timestamps_modes_shebang_and_comment(
    members: list[Member], first: Encoding, second: Encoding
) -> None:
    assert _digest(members, first) == _digest(members, second)


@st.composite
def _edited(draw: st.DrawFn) -> tuple[list[Member], list[Member]]:
    """Members, and a copy with one edit.

    The edits: flip a data byte, swap one name character for one of the same UTF-8 length, add, drop, or swap a
    member, move a byte across a member boundary, or fold the next member, with its name framing, into one member's
    data. A "~" or an "ê" is outside the name alphabet, so an edited name stays unique.
    """
    members = draw(st.lists(MEMBER, min_size=2, max_size=5, unique_by=lambda member: member[0]))
    edited = list(members)
    index = draw(st.integers(0, len(members) - 2))
    name, data = edited[index]
    following_name, following_data = edited[index + 1]
    edit = draw(st.sampled_from(["data", "name", "add", "drop", "swap", "move", "fold"]))
    if edit == "data":
        position = draw(st.integers(0, len(data)))
        edited[index] = (name, data[:position] + bytes([(data + b"\0")[position] ^ 1]) + data[position + 1 :])
    elif edit == "name":
        position = draw(st.integers(0, len(name) - 1))
        edited[index] = (name[:position] + ("~" if name[position].isascii() else "ê") + name[position + 1 :], data)
    elif edit == "add":
        edited.insert(index, ("added~", data))
    elif edit == "drop":
        del edited[index]
    elif edit == "swap":
        edited[index], edited[index + 1] = edited[index + 1], edited[index]
    elif edit == "fold":
        framing = len(following_name.encode()).to_bytes(8, "big") + following_name.encode()
        edited[index : index + 2] = [(name, data + framing + following_data)]
    elif following_data:
        edited[index] = (name, data + following_data[:1])
        edited[index + 1] = (following_name, following_data[1:])
    elif data:
        edited[index] = (name, data[:-1])
        edited[index + 1] = (following_name, data[-1:])
    else:
        edited[index] = (name, b"\0")
    return members, edited


@PROPERTY
@given(_edited(), ENCODINGS)
def test_any_change_to_a_member_name_byte_or_order_changes_the_digest(
    pair: tuple[list[Member], list[Member]], encoding: Encoding
) -> None:
    before, after = pair

    assert _digest(before, encoding) != _digest(after, encoding)
