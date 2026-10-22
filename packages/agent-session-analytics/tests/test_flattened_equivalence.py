"""Differential attack: the pre-flattened tables equal the old json_extract SQL on malformed rows.

Each test builds the database through `ingest --force`, then runs the SQL that
built the table before the staging pass flattened it (copied from the
previous release) over raw_entries, and compares the two row multisets.
"""

from __future__ import annotations

from .conftest import Sandbox, needs_duckdb

OLD_TOOL_USES = """
    WITH content_blocks AS (
        SELECT
            unnest(json_extract(json_extract(message, '$.content'), '$[*]')) AS block,
            harness, timestamp, sessionId, cwd, gitBranch
        FROM raw_entries
        WHERE type = 'assistant'
          AND message IS NOT NULL
          AND json_extract(message, '$.content') IS NOT NULL
          AND json_type(json_extract(message, '$.content')) = 'ARRAY'
    )
    SELECT
        harness,
        json_extract_string(block, '$.name') AS tool_name,
        json_extract_string(block, '$.id') AS tool_use_id,
        json_extract(block, '$.input') AS input,
        json_extract_string(block, '$.input.command') AS bash_cmd,
        json_extract_string(block, '$.input.skill') AS skill_name,
        json_extract_string(block, '$.input.args') AS skill_args,
        json_extract_string(block, '$.input.subagent_type') AS agent_type,
        json_extract_string(block, '$.input.description') AS agent_desc,
        json_extract_string(block, '$.input.mode') AS agent_mode,
        json_extract_string(block, '$.input.pattern') AS grep_pattern,
        json_extract_string(block, '$.input.file_path') AS file_path,
        json_extract_string(block, '$.input.query') AS query,
        timestamp, sessionId, cwd, gitBranch
    FROM content_blocks
    WHERE json_extract_string(block, '$.type') = 'tool_use'
"""

OLD_TOOL_RESULTS = """
    WITH content_blocks AS (
        SELECT
            unnest(json_extract(json_extract(message, '$.content'), '$[*]')) AS block,
            harness, timestamp, sessionId
        FROM raw_entries
        WHERE type = 'user'
          AND message IS NOT NULL
          AND json_type(json_extract(message, '$.content')) = 'ARRAY'
    )
    SELECT
        harness,
        json_extract_string(block, '$.tool_use_id') AS tool_use_id,
        substr(json_extract_string(block, '$.content'), 1, 500) AS content,
        coalesce(json_extract_string(block, '$.is_error'), 'false') AS is_error,
        json_extract_string(block, '$.is_error') IS NOT NULL AS is_error_explicit,
        timestamp, sessionId
    FROM content_blocks
    WHERE json_extract_string(block, '$.type') = 'tool_result'
"""

OLD_STOP_EVENTS = """
    SELECT
        harness,
        json_extract_string(message, '$.stop_reason') AS stop_reason,
        timestamp, sessionId, cwd, gitBranch
    FROM raw_entries
    WHERE type = 'assistant'
      AND message IS NOT NULL
      AND json_extract_string(message, '$.stop_reason')
          IN ('end_turn', 'stop_sequence', 'max_tokens', 'success', 'error', 'aborted')
"""

OLD_MODEL_TURNS = """
    WITH fragments AS (
        SELECT
            harness, sessionId, cwd, timestamp, message,
            coalesce(json_extract_string(message, '$.id'),
                     'row:' || row_number() OVER ()) AS turn_key,
            CASE WHEN harness = 'codex' THEN NULL ELSE
            (SELECT count(*)
               FROM unnest(json_extract(json_extract(message, '$.content'), '$[*]')) AS c(block)
              WHERE json_extract_string(block, '$.type') = 'tool_use') END AS tool_calls
        FROM raw_entries
        WHERE type IN ('assistant', 'token_usage')
          AND message IS NOT NULL
          AND json_extract_string(message, '$.model') IS NOT NULL
    )
    SELECT
        harness,
        any_value(json_extract_string(message, '$.model')) AS model,
        arg_max(json_extract_string(message, '$.stop_reason'), timestamp) AS stop_reason,
        max(json_extract_string(message, '$.error_message')) AS error_message,
        max(TRY_CAST(json_extract(message, '$.usage.input_tokens') AS BIGINT)) AS input_tokens,
        max(TRY_CAST(json_extract(message, '$.usage.output_tokens') AS BIGINT)) AS output_tokens,
        max(TRY_CAST(json_extract(message, '$.usage.cache_read_input_tokens') AS BIGINT))
            AS cache_read_tokens,
        max(TRY_CAST(json_extract(message, '$.usage.cache_creation_input_tokens') AS BIGINT))
            AS cache_creation_tokens,
        CASE WHEN harness IN ('claude', 'omp', 'pi')
            THEN max(TRY_CAST(json_extract(message, '$.usage.input_tokens') AS BIGINT))
                + coalesce(max(TRY_CAST(json_extract(message, '$.usage.cache_read_input_tokens') AS BIGINT)), 0)
                + coalesce(max(TRY_CAST(json_extract(message, '$.usage.cache_creation_input_tokens') AS BIGINT)), 0)
            ELSE max(TRY_CAST(json_extract(message, '$.usage.input_tokens') AS BIGINT))
        END AS context_tokens,
        max(TRY_CAST(json_extract(message, '$.prompt_tokens') AS BIGINT)) AS prompt_tokens,
        max(TRY_CAST(json_extract(message, '$.duration_ms') AS DOUBLE)) AS duration_ms,
        max(TRY_CAST(json_extract(message, '$.ttft_ms') AS DOUBLE)) AS ttft_ms,
        CAST(sum(tool_calls) AS BIGINT) AS tool_calls,
        min(timestamp) AS timestamp, sessionId, any_value(cwd) AS cwd
    FROM fragments
    GROUP BY harness, sessionId, turn_key
"""

OLD_SQL = {
    "tool_uses": OLD_TOOL_USES,
    "tool_results": OLD_TOOL_RESULTS,
    "stop_events": OLD_STOP_EVENTS,
    "model_turns": OLD_MODEL_TURNS,
}


def _entry(n: int, kind: str, message: object, **fields: object) -> dict[str, object]:
    return {
        "type": kind,
        "timestamp": f"2026-03-01T00:00:{n:02d}Z",
        "sessionId": "eq",
        "cwd": "/w",
        "gitBranch": "main",
        "message": message,
        **fields,
    }


def _result(content: object, **fields: object) -> dict[str, object]:
    return {"type": "tool_result", "tool_use_id": "r", "content": content, **fields}


def _malformed_entries() -> list[dict[str, object]]:
    near_clip = "y" * 498
    blocks: list[object] = [
        "bare string block",
        7,
        None,
        ["nested"],
        {"type": "tool_use"},
        {"type": "tool_use", "id": 5, "name": {"o": 1}, "input": "string input"},
        {"type": "tool_use", "id": "u2", "name": 9, "input": {}},
        {"type": "tool_use", "id": "u3", "name": "Bash", "input": ["list", "input"]},
        {
            "type": "tool_use",
            "id": "u4",
            "name": "Agent",
            "input": {
                "command": ["a", "b"],
                "skill": {"k": [1, None, True]},
                "args": 12,
                "subagent_type": None,
                "description": "café   \x01",
                "mode": False,
                "pattern": "",
                "file_path": 3.5,
                "query": {"n": -0.0},
            },
        },
        {"type": "TOOL_USE", "id": "case", "name": "X"},
    ]
    results: list[object] = [
        _result(None),
        _result(12),
        _result(True),
        _result({"k": "v", "n": [1, 2.5, None]}),
        _result([{"type": "text", "text": "café \x1f   / \\"}]),
        _result(near_clip + "\U0001F600\U0001F600\U0001F600"),
        _result(near_clip + "ééé"),
        _result("ok", is_error="yes"),
        _result("ok", is_error=1),
        _result("ok", is_error=None),
        _result("ok", is_error=False),
        {"type": "tool_result", "tool_use_id": 77},
        {"type": "tool_result"},
        "tool_result",
        [{"type": "tool_result"}],
    ]
    return [
        _entry(1, "assistant", {"content": "string content", "model": "m", "stop_reason": "end_turn"}),
        _entry(2, "assistant", {"content": {"type": "tool_use"}, "model": 0}),
        _entry(3, "assistant", {"content": blocks, "model": "m", "id": "split", "stop_reason": 5}),
        _entry(4, "assistant", {"content": blocks[5:7], "model": "m2", "id": "split", "stop_reason": "max_tokens"}),
        _entry(5, "assistant", None),
        _entry(6, "assistant", "message as a string"),
        _entry(7, "assistant", ["message", "as", "a", "list"]),
        _entry(8, "assistant", {"model": None, "stop_reason": "aborted"}),
        _entry(9, "assistant", {"model": "", "id": "", "usage": "not an object"}),
        _entry(10, "assistant", {"model": "m", "id": "", "usage": {"input_tokens": "10", "output_tokens": 2.5}}),
        _entry(
            11,
            "assistant",
            {
                "model": {"name": "m"},
                "id": 42,
                "error_message": ["e"],
                "usage": {
                    "input_tokens": 2**70,
                    "output_tokens": True,
                    "cache_read_input_tokens": -3,
                    "cache_creation_input_tokens": None,
                },
                "prompt_tokens": "many",
                "duration_ms": "12.5",
                "ttft_ms": [1],
            },
        ),
        _entry(12, "assistant", {"model": "m", "content": [{"type": "tool_use", "id": "z"}]}),
        _entry(12, "assistant", {"model": "m", "content": []}),
        # A malformed typed column (hookCount INTEGER, isSidechain BOOLEAN) keeps the row.
        _entry(13, "assistant", {"model": "m", "stop_reason": "error"}, hookCount="many", isSidechain="maybe"),
        _entry(14, "user", {"content": results}),
        _entry(15, "user", {"content": "plain prompt", "stop_reason": "end_turn", "model": "m"}),
        _entry(16, "user", {"content": {"type": "tool_result"}}),
        _entry(17, "user", None),
        _entry(18, "token_usage", {"model": "m", "usage": {"input_tokens": 1}}),
        _entry(19, "system", {"model": "m", "stop_reason": "end_turn"}),
    ]


def _difference(sandbox: Sandbox, table: str) -> list[dict[str, object]]:
    old = OLD_SQL[table]
    return sandbox.query(
        f"WITH old AS ({old}), new AS (SELECT * FROM {table}) "
        + "(SELECT 'old only' AS side, * FROM (SELECT * FROM old EXCEPT ALL SELECT * FROM new)) "
        + "UNION ALL "
        + "(SELECT 'new only' AS side, * FROM (SELECT * FROM new EXCEPT ALL SELECT * FROM old))"
    )


def _seed(sandbox: Sandbox, entries: list[dict[str, object]]) -> None:
    _ = sandbox.write_log("claude/projects/p/eq.jsonl", entries)
    _ = sandbox.write_log(
        ".omp/agent/sessions/-w/eq.jsonl",
        [
            {"type": "session", "id": "oq", "cwd": "/w"},
            {
                "type": "message",
                "timestamp": "2026-03-01T00:01:00Z",
                "message": {
                    "role": "assistant",
                    "model": "om",
                    "content": [{"type": "toolCall", "id": "k", "name": "bash", "arguments": "not json"}],
                },
            },
            {"type": "message", "timestamp": "2026-03-01T00:01:01Z", "message": {"role": "assistant", "model": "om"}},
        ],
    )
    result = sandbox.run("ingest", "--force")
    assert result.returncode == 0, result.stderr


@needs_duckdb
def test_flattened_tables_match_the_old_sql_on_malformed_rows(sandbox: Sandbox) -> None:
    _seed(sandbox, _malformed_entries())
    counts = {
        table: sandbox.query(f"SELECT count(*) AS n FROM {table}")[0]["n"] for table in OLD_SQL
    }
    # The attack must reach every table; an empty table would compare equal by accident.
    assert all(isinstance(n, int) and n > 0 for n in counts.values()), counts
    for table in OLD_SQL:
        assert _difference(sandbox, table) == [], table


@needs_duckdb
def test_tool_uses_input_keeps_an_explicit_json_null_like_the_old_sql(sandbox: Sandbox) -> None:
    # The old SQL kept the JSON value null; SQL NULL changes `input IS NULL` filters.
    block = {"type": "tool_use", "id": "n1", "name": "Bash", "input": None}
    _seed(sandbox, [_entry(1, "assistant", {"content": [block]})])
    assert _difference(sandbox, "tool_uses") == []


@needs_duckdb
def test_tool_results_content_prints_exponent_floats_like_the_old_sql(sandbox: Sandbox) -> None:
    # Python writes 1e-05 and 1e+16; DuckDB wrote 0.00001 and 10000000000000000.0.
    values: list[object] = [1e-05, 1e16, 1.5e300, 1e-7, 1.25e-6, 1e20, 1e21, -0.0, 0.0, 123.0, 1.2345678901234568e20]
    _seed(sandbox, [_entry(1, "user", {"content": [_result(values)]})])
    assert _difference(sandbox, "tool_results") == []
