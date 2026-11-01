"""Every row kind that codex_normalize yields carries the same session envelope."""

from __future__ import annotations

import pytest

from .test_codex_ingest import Row, call, normalize, output, token_count, turn_context, usage

ENVELOPE_KEYS = ["harness", "timestamp", "sessionId", "parentSessionId", "isSidechain", "version", "cwd"]


def _session_meta() -> Row:
    return {
        "timestamp": "t0",
        "type": "session_meta",
        "payload": {
            "id": "s-1",
            "cwd": "/work/x",
            "cli_version": "9.9.9",
            "source": {"subagent": {"thread_spawn": {"parent_thread_id": "p-1"}}},
        },
    }


def _prompt() -> Row:
    return {
        "timestamp": "t4",
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {"type": "UserMessage", "content": [{"type": "text", "text": "hi"}]},
        },
    }


@pytest.mark.ac("gh-407:AC-1")
def test_every_row_kind_carries_the_session_envelope() -> None:
    tokens = token_count("t5", usage(1, 2, 0), usage(1, 2, 0))
    entries = normalize([_session_meta(), turn_context("gpt-x"), call("c1"), output("c1"), _prompt(), tokens])
    assert [e["timestamp"] for e in entries] == ["t2", "t3", "t4", "t5"]
    for entry in entries:
        assert all(k in entry for k in ENVELOPE_KEYS)
        assert {k: entry[k] for k in ENVELOPE_KEYS if k != "timestamp"} == {
            "harness": "codex",
            "sessionId": "s-1",
            "parentSessionId": "p-1",
            "isSidechain": True,
            "version": "9.9.9",
            "cwd": "/work/x",
        }
