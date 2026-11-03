"""Typed readers for run-record fields reject a wrong type with a field-naming error."""
from __future__ import annotations

import pytest

from skillz_experiments.run import _record_fields as fields


def test_a_missing_field_names_the_key() -> None:
    with pytest.raises(ValueError, match="run record lacks phase"):
        _ = fields.field({}, "phase")


def test_text_field_rejects_a_non_string() -> None:
    assert fields.text_field({"phase": "gated"}, "phase") == "gated"
    with pytest.raises(ValueError, match="phase must be text"):
        _ = fields.phase({"phase": 3})


def test_judge_flag_is_text_or_null() -> None:
    assert fields.judge_flag({}) is None
    assert fields.judge_flag({"judge_model_flag": "m"}) == "m"
    with pytest.raises(ValueError, match="judge_model_flag must be text or null"):
        _ = fields.judge_flag({"judge_model_flag": 1})


def test_number_field_accepts_int_and_float_but_not_bool_or_text() -> None:
    assert fields.number_field({"n": 2}, "n") == 2
    assert fields.number_field({"n": 2.5}, "n") == 2.5
    for bad in (True, "2"):
        with pytest.raises(ValueError, match="n must be a number"):
            _ = fields.number_field({"n": bad}, "n")


def test_int_field_rejects_negative_float_and_bool() -> None:
    assert fields.int_field({"n": 0}, "n") == 0
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError, match="n must be a nonnegative integer"):
            _ = fields.int_field({"n": bad}, "n")


def test_count_map_loads_numbers_and_treats_none_as_empty() -> None:
    assert fields.count_map(None) == {}
    assert fields.count_map({"a": 1, "b": 2.5}) == {"a": 1.0, "b": 2.5}


@pytest.mark.parametrize("bad", [[1], {"a": -1}, {"a": "x"}, {"a": True}, {1: 1}])
def test_count_map_rejects_anything_but_a_map_of_nonnegative_numbers(bad: object) -> None:
    with pytest.raises(ValueError, match="role counts must map roles to nonnegative numbers"):
        _ = fields.count_map(bad)


def test_record_outcomes_requires_a_list_of_objects() -> None:
    assert fields.record_outcomes({"outcomes": [{"a": 1}]}) == [{"a": 1}]
    bad_values: list[object] = [{}, [1], "x"]
    for bad in bad_values:
        with pytest.raises(ValueError, match="outcomes"):
            _ = fields.record_outcomes({"outcomes": bad})


def test_criterion_levels_keeps_only_levels_one_to_four() -> None:
    item: dict[str, object] = {
        "criteria": {"a": {"level": 4}, "b": {"level": 5}, "c": {"level": True}, "d": "x", "e": {"level": 1}}
    }
    assert fields.criterion_levels(item) == {"a": 4, "e": 1}
    assert fields.criterion_levels({"criteria": [1]}) == {}
    assert fields.criterion_levels({}) == {}
