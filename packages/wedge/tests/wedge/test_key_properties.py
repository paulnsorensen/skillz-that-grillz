"""Property-based checks of ``compute_key``, with build-input trees that Hypothesis generates."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wedge._config import WedgeConfig
from wedge._key import compute_key

# Each example writes one or two checkouts. The autouse layer-cache fixture of conftest.py is function-scoped,
# and every example writes only inside its own temporary directory.
PROPERTY = settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])

SEGMENTS = st.from_regex(r"[a-z]{1,4}(\.py)?", fullmatch=True)
CONTENTS = st.binary(min_size=1, max_size=32)
# Files that the key must ignore: compiled caches, and files outside every configured input.
JUNK = (
    ("pkg/__pycache__/cached.cpython-313.pyc", b"cache"),
    ("pkg/stray.pyc", b"stray"),
    ("notes.txt", b"not an input"),
    ("skill/README.md", b"not an input"),
)


@dataclass(frozen=True)
class KeyCase:
    """One checkout: the source tree, an optional include tree, and the files the key digests whole."""

    source: dict[str, bytes]
    include: dict[str, bytes] | None
    uv_lock: bytes
    config: bytes
    defaults: bytes | None
    target: str | None

    def entries(self) -> list[tuple[str, bytes]]:
        """Every file of the checkout, as a path relative to its root and the file's bytes."""
        entries = [
            ("pyproject.toml", b"[project]\nname = 'x'\nversion = '0'\n"),
            ("uv.lock", self.uv_lock),
            ("skill/wedge.toml", self.config),
            *((f"pkg/{path}", data) for path, data in self.source.items()),
            *((f"inc/{path}", data) for path, data in (self.include or {}).items()),
        ]
        return entries if self.defaults is None else [*entries, ("wedge.toml", self.defaults)]

    def wedge_config(self) -> WedgeConfig:
        return WedgeConfig(
            name=self.target or "x",
            entry="x:main",
            source="../pkg",
            repo="o/r",
            project="..",
            include=() if self.include is None else ("../inc",),
            multi=self.target is not None,
        )


@st.composite
def _tree(draw: st.DrawFn) -> dict[str, bytes]:
    """Files at one to three levels; a path that is a directory of another path is dropped."""
    paths = draw(st.lists(st.lists(SEGMENTS, min_size=1, max_size=3).map(tuple), min_size=1, max_size=5, unique=True))
    files = [path for path in paths if not any(len(other) > len(path) and other[: len(path)] == path for other in paths)]
    return {"/".join(path): draw(CONTENTS) for path in files}


CASES = st.builds(
    KeyCase,
    source=_tree(),
    include=st.none() | _tree(),
    uv_lock=CONTENTS,
    config=CONTENTS,
    defaults=st.none() | CONTENTS,
    target=st.none() | st.from_regex(r"[a-z][a-z0-9-]{0,8}", fullmatch=True),
)


def _write(root: Path, entries: Sequence[tuple[str, bytes]]) -> None:
    for relative, data in entries:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)


def _key(root: Path, case: KeyCase) -> str:
    return compute_key(root / "skill", case.wedge_config())


@PROPERTY
@given(CASES, st.data())
def test_the_key_ignores_checkout_path_write_order_mtimes_and_non_inputs(case: KeyCase, data: st.DataObject) -> None:
    entries = case.entries()
    shuffled = data.draw(st.permutations([*entries, *JUNK]))
    mtimes = data.draw(st.lists(st.integers(0, 2**32), min_size=len(shuffled), max_size=len(shuffled)))
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        _write(Path(first), entries)
        _write(Path(second), shuffled)
        for (relative, _data), mtime in zip(shuffled, mtimes, strict=True):
            os.utime(Path(second) / relative, ns=(mtime * 10**9, mtime * 10**9))

        key = _key(Path(first), case)

        assert _key(Path(first), case) == key
        assert _key(Path(second), case) == key


@st.composite
def _changed(draw: st.DrawFn) -> tuple[KeyCase, KeyCase]:
    """A case, and a copy with one hashed byte flipped, one input file renamed, or another target name.

    The changed input comes first, so each input gets an even share, and the case gains that input when it lacks it.
    """
    field = draw(st.sampled_from(["uv_lock", "config", "defaults", "source", "include", "target"]))
    case = draw(CASES)
    case = replace(
        case,
        defaults=case.defaults or draw(CONTENTS),
        include=case.include or draw(_tree()),
        target=case.target or "t",
    ) if field in ("defaults", "include", "target") else case
    if field == "target":
        return case, replace(case, target=f"{case.target}0")
    if field == "uv_lock":
        return case, replace(case, uv_lock=_flip(draw, case.uv_lock))
    if field == "config":
        return case, replace(case, config=_flip(draw, case.config))
    if field == "defaults":
        return case, replace(case, defaults=_flip(draw, case.defaults or b""))
    tree = dict(case.source if field == "source" else case.include or {})
    path = draw(st.sampled_from(sorted(tree)))
    if draw(st.booleans()):
        tree[f"{path}_moved"] = tree.pop(path)
    else:
        tree[path] = _flip(draw, tree[path])
    return case, replace(case, source=tree) if field == "source" else replace(case, include=tree)


def _flip(draw: st.DrawFn, data: bytes) -> bytes:
    """``data`` with one byte changed to another value."""
    index = draw(st.integers(0, len(data) - 1))
    return data[:index] + bytes([data[index] ^ draw(st.integers(1, 255))]) + data[index + 1 :]


@PROPERTY
@given(_changed())
def test_any_change_to_a_hashed_input_changes_the_key(pair: tuple[KeyCase, KeyCase]) -> None:
    before, after = pair
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        _write(Path(first), before.entries())
        _write(Path(second), after.entries())

        assert _key(Path(first), before) != _key(Path(second), after)
