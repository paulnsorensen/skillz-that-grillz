"""Acceptance tests for the `session-analytics` CLI: exit codes, cache, lock, and schema."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest
from .conftest import Sandbox, claude_skill_entry

REQUIRE_DUCKDB = os.environ.get("REQUIRE_DUCKDB") == "1"
needs_duckdb = pytest.mark.skipif(
    not (REQUIRE_DUCKDB or shutil.which("duckdb")), reason="DuckDB CLI unavailable"
)

INSTALL_COMMANDS = (
    "uv tool install duckdb-cli",
    "brew install duckdb",
    "mise use -g duckdb",
    "curl https://install.duckdb.org | sh",
)


def _json(text: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(text))


def _seed_claude(sandbox: Sandbox) -> None:
    _ = sandbox.write_log("claude/projects/sample/session.jsonl", [claude_skill_entry(sandbox.root)])


def _codex_session(sandbox: Sandbox) -> None:
    def stamp(second: int) -> str:
        return f"2026-05-30T11:00:0{second}Z"

    usage = {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2, "total_tokens": 12}
    _ = sandbox.write_log(
        "codex/sessions/2026/05/30/rollout.jsonl",
        [
            {"timestamp": stamp(0), "type": "session_meta", "payload": {"id": "x-1", "cwd": "/w"}},
            {"timestamp": stamp(1), "type": "turn_context", "payload": {"cwd": "/w", "model": "gpt-x"}},
            {
                "timestamp": stamp(2),
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": '{"command":["ls"]}',
                    "call_id": "c1",
                },
            },
            {
                "timestamp": stamp(3),
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "c1", "output": "ok"},
            },
            {
                "timestamp": stamp(4),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": usage, "total_token_usage": usage},
                },
            },
        ],
    )


def test_missing_duckdb_exits_3_and_names_every_install_command(sandbox: Sandbox) -> None:
    for args in ("ingest",), ("sql", "SELECT 1"):
        result = sandbox.run(*args, extra_env={"PATH": "/nonexistent"})
        assert result.returncode == 3, (args, result.stderr)
        assert result.stdout == ""
        for command in INSTALL_COMMANDS:
            assert command in result.stderr, (args, command)


@needs_duckdb
def test_ingest_query_cache_hit_and_stage_cleanup(sandbox: Sandbox) -> None:
    _seed_claude(sandbox)
    first = sandbox.run("ingest")
    assert first.returncode == 0, first.stderr
    assert _json(first.stdout)["status"] == "ingested"
    assert sandbox.database.is_file()
    assert not Path(f"{sandbox.database}.stage").exists()
    query = sandbox.run(
        "sql", "SELECT count(*) AS n FROM skill_invocations WHERE skill_name = 'skillz'"
    )
    assert query.returncode == 0, query.stderr
    assert json.loads(query.stdout) == [{"n": 1}]
    cached = sandbox.run("ingest")
    assert cached.returncode == 0, cached.stderr
    assert _json(cached.stdout)["status"] == "cached"


@needs_duckdb
def test_codex_token_usage_reaches_model_turns_but_not_entry_count(sandbox: Sandbox) -> None:
    _codex_session(sandbox)
    result = sandbox.run("ingest", "--force")
    assert result.returncode == 0, result.stderr
    turns = sandbox.query("SELECT count(*) AS n FROM model_turns WHERE harness = 'codex'")
    assert turns == [{"n": 1}]
    sessions = sandbox.query("SELECT entry_count FROM sessions WHERE harness = 'codex'")
    assert sessions == [{"entry_count": 2}]


@needs_duckdb
def test_stale_schema_version_rebuilds_inside_the_cache_window(sandbox: Sandbox) -> None:
    _seed_claude(sandbox)
    assert sandbox.run("ingest").returncode == 0
    _ = subprocess.run(
        ["duckdb", str(sandbox.database), "-c", "UPDATE schema_info SET version = 0"],
        check=True,
        capture_output=True,
    )
    result = sandbox.run("ingest")
    assert result.returncode == 0, result.stderr
    assert _json(result.stdout)["status"] == "ingested"
    assert sandbox.query("SELECT version FROM schema_info") == [{"version": 1}]


@needs_duckdb
def test_sql_rejects_writes_with_exit_5_and_leaves_the_database_alone(sandbox: Sandbox) -> None:
    _seed_claude(sandbox)
    assert sandbox.run("ingest").returncode == 0
    before = hashlib.sha256(sandbox.database.read_bytes()).hexdigest()
    result = sandbox.run("sql", "CREATE TABLE sneaky AS SELECT 1 AS x")
    assert result.returncode == 5, result.stderr
    assert hashlib.sha256(sandbox.database.read_bytes()).hexdigest() == before
    tables = sandbox.query("SELECT count(*) AS n FROM duckdb_tables() WHERE table_name = 'sneaky'")
    assert tables == [{"n": 0}]


@needs_duckdb
def test_ingest_waits_for_the_lock_and_then_succeeds(sandbox: Sandbox) -> None:
    _seed_claude(sandbox)
    lock_path = Path(f"{sandbox.database}.lock")
    lock_path.parent.mkdir(parents=True)
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        process = subprocess.Popen(
            [sys.executable, "-m", "session_analytics", "ingest"],
            env=sandbox.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(1.5)
        assert process.poll() is None, "ingest must block while another process holds the lock"
        assert not sandbox.database.exists()
    finally:
        os.close(holder)
    stdout, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr
    assert _json(stdout)["status"] == "ingested"


@needs_duckdb
@pytest.mark.parametrize("has_database", [False, True])
def test_failed_auto_ingest_exits_4_and_never_queries_stale_data(
    sandbox: Sandbox, has_database: bool
) -> None:
    if has_database:
        sandbox.database.parent.mkdir(parents=True)
        sandbox.database.touch()
        os.utime(sandbox.database, (0, 0))
    result = sandbox.run("sql", "SELECT 1")
    assert result.returncode == 4, result.stderr
    assert result.stdout == ""
    assert "Automatic ingestion failed" in result.stderr
    if has_database:
        assert "stale data" in result.stderr
    else:
        assert "no session database is available" in result.stderr
        assert "stale data" not in result.stderr


@needs_duckdb
def test_sessions_db_override_is_never_refreshed(sandbox: Sandbox) -> None:
    override = sandbox.root / "custom.duckdb"
    missing = sandbox.run("sql", "SELECT 1", extra_env={"SESSIONS_DB": str(override)})
    assert missing.returncode == 4, missing.stderr
    assert "No session database" in missing.stderr

    _seed_claude(sandbox)
    built = sandbox.run("ingest", extra_env={"SESSIONS_DB": str(override)})
    assert built.returncode == 0, built.stderr
    _ = subprocess.run(
        ["duckdb", str(override), "-c", "UPDATE schema_info SET version = 0"],
        check=True,
        capture_output=True,
    )
    mismatch = sandbox.run("sql", "SELECT 1", extra_env={"SESSIONS_DB": str(override)})
    assert mismatch.returncode == 4, mismatch.stderr
    assert "schema version" in mismatch.stderr


def test_schema_command_returns_the_packaged_reference(sandbox: Sandbox) -> None:
    result = sandbox.run("schema")
    assert result.returncode == 0, result.stderr
    payload = _json(result.stdout)
    packaged = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "session_analytics"
        / "references"
        / "canonical-schema.md"
    )
    assert payload["reference"] == "canonical-schema.md"
    assert payload["content"] == packaged.read_text(encoding="utf-8")
