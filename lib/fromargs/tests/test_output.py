"""Behavior of fromargs._output.write_result: JSON serialization and truncation."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

import pytest

from fromargs._output import write_result

ITEMS = ["a", "b", "c", "d", "e"]


@dataclass
class Point:
    x: int
    y: int


def _run(value: object, *, limit: int | None = None, full: bool = False) -> tuple[str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(stderr):
        write_result(value, limit=limit, full=full, stdout=stdout)
    return stdout.getvalue(), stderr.getvalue()


def test_scalar_prints_as_json() -> None:
    out, err = _run(42)

    assert out == "42\n"
    assert err == ""


def test_dataclass_serializes_via_asdict() -> None:
    out, _ = _run(Point(1, 2))

    assert json.loads(out) == {"x": 1, "y": 2}


def test_dict_dumps_as_json() -> None:
    value = {"path": PurePosixPath("/tmp/x"), "n": 1}

    out, _ = _run(value)

    assert json.loads(out) == {"path": "/tmp/x", "n": 1}


def test_non_dict_mapping_dumps_as_a_dict() -> None:
    out, _ = _run(MappingProxyType({"k": 1}))

    assert json.loads(out) == {"k": 1}


def test_list_without_limit_is_not_truncated() -> None:
    out, err = _run(ITEMS)

    assert json.loads(out) == ITEMS
    assert err == ""


def test_list_over_limit_is_truncated_with_a_note() -> None:
    out, err = _run(ITEMS, limit=2)

    assert json.loads(out) == ["a", "b"]
    assert err == "note: showing 2 of 5; pass --full for the rest\n"


def test_full_suppresses_truncation_and_the_note() -> None:
    out, err = _run(ITEMS, limit=2, full=True)

    assert json.loads(out) == ITEMS
    assert err == ""


def test_list_at_or_under_limit_has_no_note() -> None:
    out, err = _run(["a", "b"], limit=2)

    assert json.loads(out) == ["a", "b"]
    assert err == ""


def test_tuple_is_truncated_like_a_list() -> None:
    out, err = _run(("a", "b", "c"), limit=2)

    assert json.loads(out) == ["a", "b"]
    assert "note: showing 2 of 3" in err


def test_mapping_is_never_truncated() -> None:
    out, err = _run({"a": 1, "b": 2, "c": 3}, limit=1)

    assert json.loads(out) == {"a": 1, "b": 2, "c": 3}
    assert err == ""


def test_string_is_never_truncated() -> None:
    out, err = _run("a\nb\nc", limit=1)

    assert json.loads(out) == "a\nb\nc"
    assert err == ""


def test_dataclasses_inside_a_list_serialize() -> None:
    out, _ = _run([Point(1, 2), Point(3, 4)])

    assert json.loads(out) == [{"x": 1, "y": 2}, {"x": 3, "y": 4}]


def test_none_stdout_writes_to_sys_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    write_result([1, 2], limit=None, full=False)

    assert json.loads(capsys.readouterr().out) == [1, 2]


def test_nan_is_rejected() -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        _ = _run(float("nan"))


def test_set_serializes_as_a_sorted_list() -> None:
    out, _ = _run({3, 1, 2})

    assert json.loads(out) == [1, 2, 3]


def test_frozenset_serializes_as_a_sorted_list() -> None:
    out, _ = _run(frozenset({"b", "c", "a"}))

    assert json.loads(out) == ["a", "b", "c"]


def test_set_of_mixed_types_is_deterministic() -> None:
    out, _ = _run({1, "a", 2})

    assert json.loads(out) == ["a", 1, 2]


def test_set_inside_a_dict_serializes_as_a_sorted_list() -> None:
    out, _ = _run({"tags": {"y", "x"}})

    assert json.loads(out) == {"tags": ["x", "y"]}


def test_top_level_set_is_truncated_after_sorting() -> None:
    out, err = _run({5, 4, 3, 2, 1}, limit=2)

    assert json.loads(out) == [1, 2]
    assert err == "note: showing 2 of 5; pass --full for the rest\n"


def test_bytes_is_rejected_with_a_fix_hint() -> None:
    with pytest.raises(TypeError, match="bytes is not JSON serializable; return a str"):
        _ = _run(b"abc")


def test_bytes_inside_a_dict_is_rejected() -> None:
    with pytest.raises(TypeError, match="return a str"):
        _ = _run({"k": b"abc"})


@pytest.mark.parametrize("factory", [bytearray, memoryview])
def test_bytes_like_is_rejected_with_a_fix_hint(factory: Callable[[bytes], object]) -> None:
    with pytest.raises(TypeError, match="is not JSON serializable; return a str"):
        _ = _run(factory(b"abc"))


def test_bytearray_inside_a_dict_is_rejected() -> None:
    with pytest.raises(TypeError, match="bytearray is not JSON serializable; return a str"):
        _ = _run({"k": bytearray(b"abc")})


def test_bytearray_is_not_truncated_into_an_int_list() -> None:
    with pytest.raises(TypeError, match="bytearray is not JSON serializable"):
        _ = _run(bytearray(b"abcde"), limit=2)


@pytest.mark.parametrize("seed", ["0", "1", "2", "3"])
def test_set_of_frozensets_is_deterministic_across_hash_seeds(seed: str) -> None:
    code = (
        "import io, json\n"
        "from fromargs._output import write_result\n"
        "out = io.StringIO()\n"
        "write_result({frozenset({'a','b'}), frozenset({'c'}), frozenset({'a'})},"
        " limit=None, full=False, stdout=out)\n"
        "print(json.dumps(json.loads(out.getvalue())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONHASHSEED": seed},
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout) == [["a", "b"], ["a"], ["c"]]

def test_generator_is_rejected_with_a_fix_hint() -> None:
    with pytest.raises(TypeError, match="generator is not JSON serializable; return a list"):
        _ = _run(x for x in range(3))


def test_generator_is_rejected_even_with_a_limit() -> None:
    with pytest.raises(TypeError, match="return a list instead of an iterator"):
        _ = _run(iter([1, 2, 3]), limit=1)


def test_unknown_type_keeps_the_generic_error() -> None:
    with pytest.raises(TypeError, match="Object of type object is not JSON serializable"):
        _ = _run(object())


def test_truncation_note_is_suppressed_when_the_kept_prefix_fails_to_serialize() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(stderr):
        with pytest.raises(ValueError, match="not JSON compliant"):
            write_result([float("nan"), 1, 2], limit=1, full=False, stdout=stdout)

    assert stderr.getvalue() == ""
    assert stdout.getvalue() == ""
