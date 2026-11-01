"""Property-based checks of the parsers that read untrusted JSON text, with inputs that Hypothesis generates."""
from __future__ import annotations

import json
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from skillz_experiments._cases import loads_untrusted, mapping

# Text with the characters that a line-oriented or escaping parser can get wrong.
TEXT = st.text(st.characters() | st.sampled_from(["\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}", "\x85", "\x00", "\r", "\n", '"', "\\"]),
               max_size=8)
SCALARS = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | TEXT
JSON: st.SearchStrategy[object] = st.recursive(
    SCALARS, lambda inner: st.lists(inner, max_size=3) | st.dictionaries(TEXT, inner, max_size=3), max_leaves=12)
CONTAINERS = st.lists(JSON, min_size=1, max_size=3) | st.dictionaries(TEXT, JSON, min_size=1, max_size=3)
OPENERS = st.sampled_from(["[", '{"a":', '[{"k":'])
# Python values that are not a dict: JSON values and other containers that look like one.
NOT_DICTS = (SCALARS | st.lists(JSON, max_size=3) | st.lists(TEXT, max_size=3) | st.tuples(TEXT, JSON)
             | st.frozensets(TEXT, max_size=3) | st.binary(max_size=4))
NOT_TEXT_KEYS = st.integers() | st.none() | st.booleans() | st.floats(allow_nan=False) | st.tuples(TEXT)


def _encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


@given(JSON, st.booleans())
def test_loads_untrusted_round_trips_every_json_value(value: object, ascii_only: bool) -> None:
    parsed = loads_untrusted(json.dumps(value, ensure_ascii=ascii_only))

    assert parsed == value
    assert _encoded(parsed) == _encoded(value)  # Also tells True from 1 and 1.0 from 1.


@given(OPENERS, st.integers(100_000, 200_000))
def test_loads_untrusted_reports_deep_nesting_as_a_value_error_not_a_recursion_error(opener: str, depth: int) -> None:
    with pytest.raises(ValueError, match="nested too deeply"):
        _ = loads_untrusted(opener * depth)


@given(CONTAINERS, st.data())
def test_loads_untrusted_refuses_a_truncated_container(value: object, data: st.DataObject) -> None:
    text = _encoded(value)
    cut = data.draw(st.integers(1, len(text) - 1))

    with pytest.raises(ValueError):
        _ = loads_untrusted(text[:cut])


@given(st.text() | JSON.map(_encoded)
       | st.builds(str.__add__, st.sampled_from(["", "{", "[", '"', "-", "1e", "nul"]), st.text(max_size=6)))
def test_loads_untrusted_raises_only_value_error_on_arbitrary_text(text: str) -> None:
    try:
        parsed = loads_untrusted(text)
    except ValueError:
        return
    assert _encoded(parsed) == _encoded(cast(object, json.loads(text)))


@given(NOT_DICTS)
def test_mapping_refuses_every_value_that_is_not_a_dict(value: object) -> None:
    with pytest.raises(ValueError, match="^expected a JSON object$"):
        _ = mapping(value)


@given(st.dictionaries(TEXT, JSON, max_size=3), NOT_TEXT_KEYS, JSON)
def test_mapping_refuses_a_dict_with_a_key_that_is_not_text(value: dict[str, object], key: object, item: object) -> None:
    with pytest.raises(ValueError, match="^expected a JSON object$"):
        _ = mapping({**value, key: item})


@given(st.dictionaries(TEXT, JSON, max_size=4))
def test_mapping_returns_a_dict_with_text_keys_unchanged(value: dict[str, object]) -> None:
    assert mapping(value) is value
