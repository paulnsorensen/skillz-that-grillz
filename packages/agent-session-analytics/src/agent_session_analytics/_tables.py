"""The DuckDB table build: the flattening SQL that loads the staged rows."""

from __future__ import annotations

import os
from collections.abc import Callable

from agent_session_analytics._db import SCHEMA_VERSION, STAGE_DIR, run_sql, sql_quote
from agent_session_analytics._emitters import DERIVED_TABLES, DerivedTable

# Canonical raw-entry columns the flattening SQL reads. Adapters emit a subset;
# read_json(union_by_name) fills the rest with NULL.
RAW_COLUMNS = {
    "harness": "VARCHAR",
    "type": "VARCHAR",
    "subtype": "VARCHAR",
    "timestamp": "VARCHAR",
    "sessionId": "VARCHAR",
    "uuid": "VARCHAR",
    "parentUuid": "VARCHAR",
    "message": "JSON",
    "version": "VARCHAR",
    "gitBranch": "VARCHAR",
    "slug": "VARCHAR",
    "cwd": "VARCHAR",
    "hookCount": "INTEGER",
    "hookInfos": "JSON",
    "hookErrors": "JSON",
    "preventedContinuation": "BOOLEAN",
    "stopReason": "VARCHAR",
    "hasOutput": "BOOLEAN",
    "level": "VARCHAR",
    "isSidechain": "BOOLEAN",
    "userType": "VARCHAR",
    "filename": "VARCHAR",
    "parentSessionId": "VARCHAR",
}


def derived_path(table: str) -> str:
    return os.path.join(STAGE_DIR, "derived", f"{table}.jsonl")


def _read_derived(table: DerivedTable) -> str:
    """Return the SQL source that reads one table's staged rows with typed columns."""
    columns = "{" + ", ".join(f"{k}: '{v}'" for k, v in table.columns.items()) + "}"
    return (
        f"read_json('{sql_quote(derived_path(table.name))}', "
        f"format='newline_delimited', ignore_errors=true, columns={columns})"
    )


def columns_struct() -> str:
    return "{" + ", ".join(f"{k}: '{v}'" for k, v in RAW_COLUMNS.items()) + "}"


def build_tables(log: Callable[[str], None]) -> None:
    """Create every table, index, and the schema marker from the staged rows."""
    stage_glob = os.path.join(STAGE_DIR, "*.jsonl")

    # Step 1: union every harness's canonical JSONL into raw_entries.
    # The JSON parser holds a few wide messages per thread (up to 9 MB each,
    # measured). One thread cuts peak memory from 4.5 GB to 2.5 GB and costs no
    # wall time. Each run is its own DuckDB process, so the setting stays local.
    run_sql(f"""
        SET threads = 1;
        CREATE TABLE raw_entries AS
        SELECT *
        FROM read_json(
            '{sql_quote(stage_glob)}',
            format='newline_delimited',
            union_by_name=true,
            ignore_errors=true,
            columns={columns_struct()}
        );
    """)

    # The next four tables read the narrow rows that stage_harnesses() wrote,
    # not raw_entries.message. JSON columns hold the raw value at each path.
    # json_extract_string(col, '$') returns the same text that
    # json_extract_string(message, '<path>') returned.
    tables = {table.name: table for table in DERIVED_TABLES}

    # Step 2: tool_uses (flattened from assistant content blocks).
    log("  Creating tool_uses...")
    run_sql(f"""
        CREATE TABLE tool_uses AS
        SELECT
            harness,
            json_extract_string(tool_name, '$') AS tool_name,
            json_extract_string(tool_use_id, '$') AS tool_use_id,
            json_extract(input, '$[0]') AS input,
            json_extract_string(bash_cmd, '$') AS bash_cmd,
            json_extract_string(skill_name, '$') AS skill_name,
            json_extract_string(skill_args, '$') AS skill_args,
            json_extract_string(agent_type, '$') AS agent_type,
            json_extract_string(agent_desc, '$') AS agent_desc,
            json_extract_string(agent_mode, '$') AS agent_mode,
            json_extract_string(grep_pattern, '$') AS grep_pattern,
            json_extract_string(file_path, '$') AS file_path,
            json_extract_string(query, '$') AS query,
            timestamp, sessionId, cwd, gitBranch
        FROM {_read_derived(tables["tool_uses"])};
    """)

    # Step 3: tool_results (from user message content blocks).
    # Claude omits is_error on most successful results (measured: ~99.5% of
    # absent-flag blocks carry non-error content), so an absent flag backfills
    # to explicit 'false' rather than a NULL every string compare treats as
    # success silently. is_error_explicit preserves whether the source block
    # carried the flag, so coverage stays measurable (issue #704).
    # The staging pass already cut content to 500 characters.
    log("  Creating tool_results...")
    run_sql(f"""
        CREATE TABLE tool_results AS
        SELECT
            harness,
            json_extract_string(tool_use_id, '$') AS tool_use_id,
            content,
            coalesce(json_extract_string(is_error, '$'), 'false') AS is_error,
            json_extract_string(is_error, '$') IS NOT NULL AS is_error_explicit,
            timestamp, sessionId
        FROM {_read_derived(tables["tool_results"])};
    """)

    # Step 4: stop_events. The staging pass kept only the listed stop reasons.
    log("  Creating stop_events...")
    run_sql(f"""
        CREATE TABLE stop_events AS
        SELECT harness, stop_reason, timestamp, sessionId, cwd, gitBranch
        FROM {_read_derived(tables["stop_events"])};
    """)

    # Step 4b: model_turns (one row per assistant turn that names a model).
    # Claude writes one raw entry per content block of a message, and repeats
    # `usage` on each entry, so entries group by `message.id`: tool calls sum,
    # usage takes one value. omp and other harnesses carry no `message.id` and
    # keep one row per entry (the staging pass gives each a unique row_key).
    # TRY_CAST keeps one malformed metric from aborting the whole ingest.
    # Latency and prompt_tokens are omp-only.
    # Codex token rows carry no content, so tool_calls stays NULL (not measured).
    # context_tokens is the prompt size the model read: Claude's and the
    # Pi family's (omp, pi) input_tokens exclude cache reads and creation, so
    # they are added; Codex's input_tokens already includes cached tokens.
    log("  Creating model_turns...")
    run_sql(f"""
        CREATE TABLE model_turns AS
        WITH fragments AS (
            SELECT
                harness, sessionId, cwd, timestamp,
                coalesce(json_extract_string(message_id, '$'), row_key) AS turn_key,
                json_extract_string(model, '$') AS model_name,
                json_extract_string(stop_reason, '$') AS stop_reason_text,
                json_extract_string(error_message, '$') AS error_text,
                TRY_CAST(input_tokens AS BIGINT) AS in_tokens,
                TRY_CAST(output_tokens AS BIGINT) AS out_tokens,
                TRY_CAST(cache_read_tokens AS BIGINT) AS cache_read,
                TRY_CAST(cache_creation_tokens AS BIGINT) AS cache_creation,
                TRY_CAST(prompt_tokens AS BIGINT) AS prompt,
                TRY_CAST(duration_ms AS DOUBLE) AS duration,
                TRY_CAST(ttft_ms AS DOUBLE) AS ttft,
                tool_calls
            FROM {_read_derived(tables["model_turns"])}
        )
        SELECT
            harness,
            any_value(model_name) AS model,
            arg_max(stop_reason_text, timestamp) AS stop_reason,
            max(error_text) AS error_message,
            max(in_tokens) AS input_tokens,
            max(out_tokens) AS output_tokens,
            max(cache_read) AS cache_read_tokens,
            max(cache_creation) AS cache_creation_tokens,
            CASE WHEN harness IN ('claude', 'omp', 'pi')
                THEN max(in_tokens)
                    + coalesce(max(cache_read), 0)
                    + coalesce(max(cache_creation), 0)
                ELSE max(in_tokens)
            END AS context_tokens,
            max(prompt) AS prompt_tokens,
            max(duration) AS duration_ms,
            max(ttft) AS ttft_ms,
            CAST(sum(tool_calls) AS BIGINT) AS tool_calls,
            min(timestamp) AS timestamp, sessionId, any_value(cwd) AS cwd
        FROM fragments
        GROUP BY harness, sessionId, turn_key;
    """)

    # Step 4c: user_turns (one row per real user prompt; see _emitters).
    # turn_index counts real prompts per harness and session, ordered by
    # timestamp, then by source file and position in that file.
    log("  Creating user_turns...")
    run_sql(f"""
        CREATE TABLE user_turns AS
        SELECT
            harness, sessionId, timestamp,
            row_number() OVER (
                PARTITION BY harness, sessionId
                ORDER BY TRY_CAST(timestamp AS TIMESTAMPTZ) NULLS LAST, source, ordinal
            ) - 1 AS turn_index,
            text, prev_assistant_text
        FROM {_read_derived(tables["user_turns"])};
    """)

    # Step 5: agent_spawns.
    log("  Creating agent_spawns...")
    run_sql("""
        CREATE TABLE agent_spawns AS
        SELECT
            harness,
            coalesce(agent_type, 'general-purpose') AS agent_type,
            agent_desc AS description,
            agent_mode AS mode,
            tool_use_id AS anchor_id,
            timestamp, sessionId, cwd
        FROM tool_uses
        WHERE tool_name IN ('Agent', 'Task');
    """)

    # Step 6: skill_invocations.
    log("  Creating skill_invocations...")
    run_sql("""
        CREATE TABLE skill_invocations AS
        SELECT
            harness, skill_name, skill_args AS args, tool_use_id AS anchor_id,
            timestamp, sessionId, cwd
        FROM tool_uses
        WHERE tool_name = 'Skill';
    """)

    # Step 7: mcp_calls.
    log("  Creating mcp_calls...")
    run_sql("""
        CREATE TABLE mcp_calls AS
        SELECT * FROM tool_uses WHERE tool_name LIKE 'mcp__%';
    """)

    # Step 8: sessions summary.
    log("  Creating sessions...")
    run_sql("""
        CREATE TABLE sessions AS
        SELECT
            harness, sessionId,
            min(timestamp) AS first_seen,
            max(timestamp) AS last_seen,
            cwd AS project,
            gitBranch AS branch,
            arg_max(version, timestamp) FILTER (WHERE version IS NOT NULL) AS version,
            max(parentSessionId) AS parent_session_id,
            count(*) FILTER (WHERE type IS DISTINCT FROM 'token_usage') AS entry_count
        FROM raw_entries
        WHERE sessionId IS NOT NULL
          AND timestamp IS NOT NULL
        GROUP BY harness, sessionId, cwd, gitBranch;
    """)

    # Step 9: stop_hooks (claude-only fields, harness threaded through).
    log("  Creating stop_hooks...")
    run_sql("""
        CREATE TABLE stop_hooks AS
        SELECT
            harness, timestamp, sessionId, hookCount, hookInfos, hookErrors,
            preventedContinuation, stopReason, hasOutput, level
        FROM raw_entries
        WHERE type = 'system' AND subtype = 'stop_hook_summary';
    """)

    # Step 10: permission_denials.
    log("  Creating permission_denials...")
    run_sql("""
        CREATE TABLE permission_denials AS
        SELECT harness, content, sessionId, timestamp
        FROM tool_results
        WHERE content LIKE 'Permission to use % has been denied%'
           OR content LIKE 'Hook PreToolUse:% denied this tool%'
           OR content LIKE '%The user doesn''t want to proceed%';
    """)

    # Step 11: interrupt_events (one row per structured interrupt marker; the
    # staging pass applies the marker rule, see _emitters).
    log("  Creating interrupt_events...")
    run_sql(f"""
        CREATE TABLE interrupt_events AS
        SELECT harness, sessionId, timestamp
        FROM {_read_derived(tables["interrupt_events"])};
    """)

    log("  Creating indexes...")
    run_sql("""
        CREATE INDEX idx_tool_uses_name ON tool_uses(tool_name);
        CREATE INDEX idx_tool_uses_session ON tool_uses(sessionId);
        CREATE INDEX idx_tool_uses_harness ON tool_uses(harness);
        CREATE INDEX idx_tool_results_error ON tool_results(is_error);
        CREATE INDEX idx_sessions_id ON sessions(sessionId);
    """)

    run_sql(
        f"""
        CREATE TABLE schema_info AS SELECT {SCHEMA_VERSION} AS version;
    """
    )
