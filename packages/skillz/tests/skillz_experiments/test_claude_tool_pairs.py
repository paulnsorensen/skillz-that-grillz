from __future__ import annotations

import pytest

from skillz_experiments.harness._claude._pairs import tool_pairs


def _use(use_id: str, name: str, **fields: object) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": use_id, "name": name, "input": fields}]}}


def _result(use_id: str, text: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}}


@pytest.mark.ac("gh-397:AC-1")
def test_tool_pairs_pairs_a_use_with_its_result_in_stream_order() -> None:
    answer = {"type": "tool_result", "tool_use_id": "a", "content": "out"}
    events: list[dict[str, object]] = [_use("a", "Bash", command="ls"), {"type": "user", "message": {"content": [answer]}}]
    pairs = list(tool_pairs(events))
    assert [(use["id"], result) for use, result in pairs] == [("a", None), ("a", answer)]


@pytest.mark.ac("gh-397:AC-1")
def test_tool_pairs_yields_a_use_without_a_result_once_and_unpaired() -> None:
    pairs = list(tool_pairs([_use("a", "Bash"), _result("other", "x")]))
    assert [(use["id"], result) for use, result in pairs] == [("a", None)]


@pytest.mark.ac("gh-397:AC-1")
def test_tool_pairs_filters_uses_and_their_results_by_name() -> None:
    events = [_use("a", "Bash"), _use("b", "Read"), _result("b", "r"), _result("a", "b")]
    pairs = list(tool_pairs(events, "Read"))
    assert [(use["name"], result is not None) for use, result in pairs] == [("Read", False), ("Read", True)]


@pytest.mark.ac("gh-397:AC-1")
def test_tool_pairs_pairs_a_result_that_arrives_in_a_later_message() -> None:
    events: list[dict[str, object]] = [_use("a", "Bash"), {"type": "system"}, _use("b", "Bash"), _result("b", "two"), _result("a", "one")]
    answered = [(use["id"], result["content"]) for use, result in tool_pairs(events) if result is not None]
    assert answered == [("b", "two"), ("a", "one")]


@pytest.mark.ac("gh-397:AC-1")
def test_tool_pairs_skips_malformed_blocks_without_raising() -> None:
    events: list[dict[str, object]] = [{"message": {"content": ["text", 3, None]}}, {"message": "x"}, {"message": {"content": "x"}}]
    assert list(tool_pairs(events)) == []
