"""The mutation runner selects the same tests per criterion as before the source-qualified markers."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

SOURCE = "fromargs-cli-library"
MUTATE = Path(__file__).parent / "e2e" / "mutate.py"
# Node ids per criterion in e2e/test_acceptance.py at the commit before the migration
# (measured with FROMARGS_AC_DUMP=1 pytest tests/e2e --collect-only).
NODE_PREFIX = "tests/e2e/test_acceptance.py::"
TESTS_PER_CRITERION = {
    "AC-1": {
        "test_ac01_top_level_command_runs_once",
        "test_ac01_group_command_runs_once",
    },
    "AC-2": {
        "test_ac02_int_result_prints_as_one_json_document",
        "test_ac02_dict_result_prints_as_one_json_document",
    },
    "AC-3": {
        "test_ac03_none_result_prints_nothing_and_exits_zero",
    },
    "AC-4": {
        "test_ac04_cli_error_exits_two_as_one_json_line",
        "test_ac04_contract_error_exits_three_as_one_json_line",
        "test_ac04_parse_failure_exits_two_as_one_json_line",
    },
    "AC-5": {
        "test_ac05_json_flag_is_a_noop_anywhere_before_the_marker[leading]",
        "test_ac05_json_flag_is_a_noop_anywhere_before_the_marker[mid]",
        "test_ac05_json_flag_is_a_noop_anywhere_before_the_marker[trailing]",
    },
    "AC-6": {
        "test_ac06_full_flag_anywhere_turns_off_truncation[leading]",
        "test_ac06_full_flag_anywhere_turns_off_truncation[trailing]",
        "test_ac06_full_revealed_by_a_quote_split_is_honored",
    },
    "AC-7": {
        "test_ac07_result_longer_than_limit_is_truncated_with_a_note",
        "test_ac07_full_prints_the_whole_result_with_no_note",
    },
    "AC-8": {
        "test_ac08_reserved_parameter_raises_at_registration[json]",
        "test_ac08_reserved_parameter_raises_at_registration[full]",
    },
    "AC-9": {
        "test_ac09_public_surface_is_exactly_five_names",
    },
    "AC-10": {
        "test_ac10_help_advertises_no_json_input_or_stdin_flag",
        "test_ac10_stdin_with_invalid_payload_does_not_change_behavior",
    },
    "AC-11": {
        "test_ac11_single_verified_candidate_is_split_and_noted",
        "test_ac11_zero_candidates_keeps_argv_and_fails_normally",
        "test_ac11_several_candidates_keeps_argv_and_fails_normally",
        "test_ac11_plain_str_option_value_is_never_split",
        "test_ac11_token_after_double_dash_is_never_split",
    },
    "AC-12": {
        "test_ac12_handler_runs_exactly_once_after_a_healed_split",
    },
    "AC-13": {
        "test_ac13_unknown_command_suggests_the_closest_match",
        "test_ac13_near_miss_option_suggests_the_closest_match",
    },
    "AC-14": {
        "test_ac14_underscore_flag_binds_max_count",
    },
    "AC-15": {
        "test_ac15_version_resolves_from_the_calling_module",
    },
    "AC-16": {
        "test_ac16_default_handler_runs_once_when_the_group_gets_no_subcommand",
        "test_ac16_default_yields_to_a_named_command_in_the_group",
    },
    "AC-17": {
        "test_ac17_group_kwargs_reach_the_nested_cyclopts_app",
    },
}


def _load_mutate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fromargs_mutate", MUTATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve string annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


class _Mutation(Protocol):
    ac_id: str
    is_na: bool


def _mutated_criteria(mutate: ModuleType) -> list[str]:
    """Return the ac_id of every mutation that edits a source file (not the n/a ones)."""
    mutations = cast(list[_Mutation], getattr(mutate, "MUTATIONS"))
    return [mutation.ac_id for mutation in mutations if not mutation.is_na]


@pytest.mark.ac("gh-267:AC-7")
def test_each_mutation_criterion_selects_the_pre_migration_tests() -> None:
    mutate = _load_mutate()
    collect = cast("Callable[[], dict[str, list[str]]]", getattr(mutate, "collect_node_ids"))
    selected = collect()
    assert {key: set(nodes) for key, nodes in selected.items()} == {
        f"{SOURCE}:{criterion}": {NODE_PREFIX + name for name in names} for criterion, names in TESTS_PER_CRITERION.items()
    }
    for criterion in _mutated_criteria(mutate):
        assert selected.get(criterion), criterion
