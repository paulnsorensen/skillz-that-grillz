"""Behavior of fromargs.emit: JSON and text output with truncation."""

from __future__ import annotations

import io
import json
from pathlib import PurePosixPath
from types import MappingProxyType

import pytest

import fromargs

ITEMS = ["a", "b", "c", "d", "e"]


def test_json_mode_dumps(capsys: pytest.CaptureFixture[str]) -> None:
    fromargs.emit(ITEMS, json_mode=True, limit=2)

    assert capsys.readouterr().out == json.dumps(ITEMS, indent=2) + "\n"


def test_dict_dumps_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    value = {"path": PurePosixPath("/tmp/x"), "n": 1}

    fromargs.emit(value, limit=1)

    assert capsys.readouterr().out == json.dumps(value, indent=2, default=str) + "\n"


def test_truncation_line(capsys: pytest.CaptureFixture[str]) -> None:
    fromargs.emit(ITEMS, limit=2)

    assert capsys.readouterr().out.splitlines() == [
        "a",
        "b",
        "... showing 2 of 5; pass --full for the rest (limit=2)",
    ]


def test_full_prints_everything(capsys: pytest.CaptureFixture[str]) -> None:
    fromargs.emit(ITEMS, limit=2, full=True)

    assert capsys.readouterr().out.splitlines() == [
        *ITEMS,
        "... showing 5 of 5 (--full; default limit=2)",
    ]


def test_short_list_has_no_truncation_line(capsys: pytest.CaptureFixture[str]) -> None:
    fromargs.emit(["a"], limit=2)

    assert capsys.readouterr().out == "a\n"


def test_list_at_limit_has_no_truncation_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fromargs.emit(["a", "b"], limit=2)

    assert capsys.readouterr().out == "a\nb\n"


def test_json_mode_string_is_not_truncated(capsys: pytest.CaptureFixture[str]) -> None:
    fromargs.emit("a\nb\nc", json_mode=True, limit=1)

    assert capsys.readouterr().out == json.dumps("a\nb\nc", indent=2) + "\n"


def test_multiline_string_is_truncated_by_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fromargs.emit("a\nb\nc", limit=1)

    assert capsys.readouterr().out.splitlines() == [
        "a",
        "... showing 1 of 3; pass --full for the rest (limit=1)",
    ]


def test_scalar_prints_plainly_to_given_stream() -> None:
    buffer = io.StringIO()

    fromargs.emit(42, stdout=buffer)

    assert buffer.getvalue() == "42\n"


def test_tuple_is_truncated_like_a_list(capsys: pytest.CaptureFixture[str]) -> None:
    fromargs.emit(("a", "b", "c"), limit=2)

    assert capsys.readouterr().out.splitlines() == [
        "a",
        "b",
        "... showing 2 of 3; pass --full for the rest (limit=2)",
    ]


def test_non_dict_mapping_dumps_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    value = MappingProxyType({"k": 1})

    fromargs.emit(value)

    assert capsys.readouterr().out == json.dumps(dict(value), indent=2) + "\n"


def test_negative_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        fromargs.emit([1, 2, 3, 4, 5], limit=-1)
