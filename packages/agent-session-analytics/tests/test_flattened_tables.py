"""Exact rows of the tables that the staging pass flattens: tool_uses, tool_results, stop_events, model_turns."""

from __future__ import annotations

from .conftest import Sandbox, claude_skill_entry, needs_duckdb

LONG_TEXT = "x" * 600


def _claude_entry(kind: str, stamp: str, message: object) -> dict[str, object]:
    return {"type": kind, "timestamp": stamp, "sessionId": "s1", "cwd": "/w", "gitBranch": "main", "message": message}


def _seed(sandbox: Sandbox) -> None:
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 2,
    }
    # One Claude message is split over two entries that repeat id and usage.
    first_half = {
        "id": "m1",
        "model": "claude-x",
        "usage": usage,
        "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}],
    }
    second_half = {
        "id": "m1",
        "model": "claude-x",
        "usage": usage,
        "stop_reason": "end_turn",
        "content": [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/a"}}],
    }
    _ = sandbox.write_log(
        "claude/projects/p/session.jsonl",
        [
            _claude_entry("assistant", "2026-01-01T00:00:01Z", first_half),
            _claude_entry("assistant", "2026-01-01T00:00:02Z", second_half),
            _claude_entry(
                "user",
                "2026-01-01T00:00:03Z",
                {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": LONG_TEXT}]},
            ),
            _claude_entry(
                "user",
                "2026-01-01T00:00:04Z",
                {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t2",
                            "is_error": True,
                            "content": [{"type": "text", "text": "café"}],
                        },
                        {"type": "tool_result", "tool_use_id": "t3", "is_error": False, "content": "ok"},
                        {"type": "text", "text": "not a result"},
                    ]
                },
            ),
            _claude_entry("user", "2026-01-01T00:00:05Z", {"content": "plain prompt text"}),
            _claude_entry("assistant", "2026-01-01T00:00:06Z", {"content": [{"type": "text", "text": "no model"}]}),
        ],
    )
    usage_codex = {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2}
    _ = sandbox.write_log(
        "codex/sessions/2026/01/01/rollout.jsonl",
        [
            {"timestamp": "2026-01-01T00:01:00Z", "type": "session_meta", "payload": {"id": "c1", "cwd": "/c"}},
            {"timestamp": "2026-01-01T00:01:01Z", "type": "turn_context", "payload": {"model": "gpt-x"}},
            {
                "timestamp": "2026-01-01T00:01:02Z",
                "type": "response_item",
                "payload": {"type": "function_call", "name": "shell", "arguments": '{"command":["pwd"]}', "call_id": "k1"},
            },
            {
                "timestamp": "2026-01-01T00:01:03Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": usage_codex, "total_token_usage": usage_codex},
                },
            },
        ],
    )
    ingested = sandbox.run("ingest", "--force")
    assert ingested.returncode == 0, ingested.stderr


@needs_duckdb
def test_tool_uses_rows_keep_every_extracted_field(sandbox: Sandbox) -> None:
    _seed(sandbox)
    rows = sandbox.query(
        "SELECT harness, tool_name, tool_use_id, CAST(input AS VARCHAR) AS input, bash_cmd, file_path, "
        + "timestamp, sessionId, cwd, gitBranch FROM tool_uses ORDER BY timestamp"
    )
    assert rows == [
        {
            "harness": "claude", "tool_name": "Bash", "tool_use_id": "t1", "input": '{"command":"ls"}',
            "bash_cmd": "ls", "file_path": None, "timestamp": "2026-01-01T00:00:01Z", "sessionId": "s1",
            "cwd": "/w", "gitBranch": "main",
        },
        {
            "harness": "claude", "tool_name": "Read", "tool_use_id": "t2", "input": '{"file_path":"/a"}',
            "bash_cmd": None, "file_path": "/a", "timestamp": "2026-01-01T00:00:02Z", "sessionId": "s1",
            "cwd": "/w", "gitBranch": "main",
        },
        {
            "harness": "codex", "tool_name": "shell", "tool_use_id": "k1",
            "input": '{"command":"pwd"}', "bash_cmd": "pwd", "file_path": None,
            "timestamp": "2026-01-01T00:01:02Z", "sessionId": "c1", "cwd": "/c", "gitBranch": None,
        },
    ]


@needs_duckdb
def test_tool_results_clip_content_and_backfill_a_missing_is_error(sandbox: Sandbox) -> None:
    _seed(sandbox)
    rows = sandbox.query(
        "SELECT tool_use_id, content, is_error, is_error_explicit FROM tool_results "
        + "WHERE harness = 'claude' ORDER BY timestamp, tool_use_id"
    )
    assert rows == [
        {"tool_use_id": "t1", "content": LONG_TEXT[:500], "is_error": "false", "is_error_explicit": False},
        {
            "tool_use_id": "t2",
            "content": '[{"type":"text","text":"café"}]',
            "is_error": "true",
            "is_error_explicit": True,
        },
        {"tool_use_id": "t3", "content": "ok", "is_error": "false", "is_error_explicit": True},
    ]


@needs_duckdb
def test_stop_events_keep_only_listed_stop_reasons(sandbox: Sandbox) -> None:
    _seed(sandbox)
    rows = sandbox.query("SELECT harness, stop_reason, timestamp, sessionId, cwd, gitBranch FROM stop_events")
    assert rows == [
        {
            "harness": "claude", "stop_reason": "end_turn", "timestamp": "2026-01-01T00:00:02Z",
            "sessionId": "s1", "cwd": "/w", "gitBranch": "main",
        }
    ]


@needs_duckdb
def test_model_turns_group_split_messages_and_leave_codex_tool_calls_null(sandbox: Sandbox) -> None:
    _seed(sandbox)
    rows = sandbox.query(
        "SELECT harness, model, stop_reason, input_tokens, output_tokens, cache_read_tokens, "
        + "cache_creation_tokens, context_tokens, tool_calls, timestamp, sessionId, cwd "
        + "FROM model_turns ORDER BY harness"
    )
    assert rows == [
        {
            "harness": "claude", "model": "claude-x", "stop_reason": "end_turn", "input_tokens": 10,
            "output_tokens": 5, "cache_read_tokens": 3, "cache_creation_tokens": 2, "context_tokens": 15,
            "tool_calls": 2, "timestamp": "2026-01-01T00:00:01Z", "sessionId": "s1", "cwd": "/w",
        },
        {
            "harness": "codex", "model": "gpt-x", "stop_reason": None, "input_tokens": 10,
            "output_tokens": 2, "cache_read_tokens": 4, "cache_creation_tokens": None, "context_tokens": 10,
            "tool_calls": None, "timestamp": "2026-01-01T00:01:03Z", "sessionId": "c1", "cwd": "/c",
        },
    ]


@needs_duckdb
def test_empty_derived_tables_keep_their_columns(sandbox: Sandbox) -> None:
    _ = sandbox.write_log("claude/projects/p/session.jsonl", [claude_skill_entry(sandbox.root)])
    assert sandbox.run("ingest", "--force").returncode == 0
    assert sandbox.query("SELECT count(*) AS n FROM tool_results") == [{"n": 0}]
    columns = sandbox.query(
        "SELECT column_name, data_type FROM duckdb_columns() WHERE table_name = 'tool_results' "
        + "ORDER BY column_index"
    )
    assert columns == [
        {"column_name": "harness", "data_type": "VARCHAR"},
        {"column_name": "tool_use_id", "data_type": "VARCHAR"},
        {"column_name": "content", "data_type": "VARCHAR"},
        {"column_name": "is_error", "data_type": "VARCHAR"},
        {"column_name": "is_error_explicit", "data_type": "BOOLEAN"},
        {"column_name": "timestamp", "data_type": "VARCHAR"},
        {"column_name": "sessionId", "data_type": "VARCHAR"},
    ]
