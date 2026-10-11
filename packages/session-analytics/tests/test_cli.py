"""Acceptance tests for the `session-analytics` CLI: exit codes, cache, lock, and schema."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest
from session_analytics import _db

from .conftest import Sandbox, claude_skill_entry, needs_duckdb

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

def _set_schema_version(database: Path, version: int) -> None:
    _ = subprocess.run(
        ["duckdb", str(database), "-c", f"UPDATE schema_info SET version = {version}"],
        check=True,
        capture_output=True,
    )


def _second_skill_entry(root: Path) -> dict[str, object]:
    entry = claude_skill_entry(root)
    entry["sessionId"] = "second-session"
    message = cast(dict[str, list[dict[str, object]]], entry["message"])
    message["content"][0]["id"] = "second-call"
    return entry


@needs_duckdb
@pytest.mark.parametrize("state", ["absent", "old_mtime", "schema_version_0"])
def test_sql_auto_ingests_an_absent_old_or_wrong_version_default_database(
    sandbox: Sandbox, state: str
) -> None:
    _seed_claude(sandbox)
    before = 0.0
    if state != "absent":
        assert sandbox.run("ingest").returncode == 0
        if state == "old_mtime":
            os.utime(sandbox.database, (0, 0))
        else:
            _set_schema_version(sandbox.database, 0)
        before = sandbox.database.stat().st_mtime
    appended = state == "old_mtime"
    if appended:
        _ = sandbox.write_log(
            "claude/projects/sample/second.jsonl", [_second_skill_entry(sandbox.root)]
        )
    result = sandbox.run("sql", "SELECT version FROM schema_info")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [{"version": 1}]
    assert sandbox.database.stat().st_mtime > before
    if appended:
        counted = sandbox.query("SELECT count(*) AS n FROM skill_invocations")
        assert counted == [{"n": 2}]


@needs_duckdb
def test_sql_refresh_does_not_run_the_ingest_summary(sandbox: Sandbox) -> None:
    _seed_claude(sandbox)
    script = (
        "import json; from session_analytics import _ingest, _query\n"
        + "def boom():\n    raise RuntimeError('summary ran')\n"
        + "_ingest._summary = boom\n"
        + "print(json.dumps(_query.run_query('SELECT 1 AS x')))"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=sandbox.env,
        cwd=sandbox.root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [{"x": 1}]


@needs_duckdb
@pytest.mark.parametrize("args", [("ingest",), ("sql", "SELECT 1")])
def test_unwritable_cache_directory_exits_4_without_a_traceback(
    sandbox: Sandbox, args: tuple[str, ...]
) -> None:
    blocker = sandbox.root / "blocker"
    _ = blocker.write_text("a file where the cache directory should go")
    result = sandbox.run(*args, extra_env={"XDG_CACHE_HOME": str(blocker)})
    assert result.returncode == 4, result.stderr
    assert result.stdout == ""
    assert "Cannot build the session database" in result.stderr
    assert "Traceback" not in result.stderr


@needs_duckdb
def test_duckdb_timeout_becomes_a_database_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "timeout.duckdb"))
    monkeypatch.setattr(_db, "DUCKDB_TIMEOUT_SECONDS", 0.0001)
    with pytest.raises(_db.DatabaseError, match="timed out"):
        _ = _db.query_rows("SELECT 1")


@needs_duckdb
def test_unreadable_database_file_exits_4_with_the_duckdb_message_and_stays_untouched(
    sandbox: Sandbox,
) -> None:
    sandbox.database.parent.mkdir(parents=True)
    _ = sandbox.database.write_text("this is not a DuckDB database")
    before = sandbox.database.read_bytes()
    for args in (("ingest",), ("sql", "SELECT 1")):
        result = sandbox.run(*args)
        assert result.returncode == 4, (args, result.stderr)
        assert result.stdout == ""
        assert "not a valid DuckDB database file" in result.stderr
    assert sandbox.database.read_bytes() == before


@needs_duckdb
def test_database_without_schema_info_rebuilds(sandbox: Sandbox) -> None:
    _seed_claude(sandbox)
    sandbox.database.parent.mkdir(parents=True)
    _ = subprocess.run(
        ["duckdb", str(sandbox.database), "-c", "CREATE TABLE other AS SELECT 1 AS x"],
        check=True,
        capture_output=True,
    )
    result = sandbox.run("ingest")
    assert result.returncode == 0, result.stderr
    assert _json(result.stdout)["status"] == "ingested"


@needs_duckdb
@pytest.mark.parametrize("kind", ["shell", "copy_to", "read_text"])
def test_sql_safe_mode_blocks_shell_file_writes_and_outside_reads(
    sandbox: Sandbox, kind: str
) -> None:
    _seed_claude(sandbox)
    assert sandbox.run("ingest").returncode == 0
    marker = sandbox.root / "marker.txt"
    outside = sandbox.root / "outside.txt"
    _ = outside.write_text("secret-content")
    queries = {
        "shell": f".shell touch {marker}",
        "copy_to": f"COPY (SELECT 1 AS x) TO '{marker}'",
        "read_text": f"SELECT content FROM read_text('{outside}')",
    }
    result = sandbox.run("sql", queries[kind])
    assert result.returncode == 5, result.stderr
    assert result.stdout == ""
    assert "secret-content" not in result.stderr
    assert not marker.exists()


@needs_duckdb
def test_sql_serves_select_and_applies_the_memory_limit_before_safe_mode(
    sandbox: Sandbox,
) -> None:
    _seed_claude(sandbox)
    assert sandbox.run("ingest").returncode == 0
    query = "SELECT current_setting('memory_limit') AS memory_limit"
    limits: list[str] = []
    for env in ({"SESSIONS_DUCKDB_MEMORY_LIMIT": "1GB"}, {"SESSIONS_DUCKDB_MEMORY_LIMIT": "2GB"}, {}):
        result = sandbox.run("sql", query, extra_env=env)
        assert result.returncode == 0, result.stderr
        limits.append(result.stdout)
    assert len(set(limits)) == 3


@needs_duckdb
def test_invalid_memory_limit_fails_ingest_with_exit_4_and_removes_the_stage(
    sandbox: Sandbox,
) -> None:
    _seed_claude(sandbox)
    result = sandbox.run(
        "ingest", "--force", extra_env={"SESSIONS_DUCKDB_MEMORY_LIMIT": "not-a-size"}
    )
    assert result.returncode == 4, result.stderr
    assert "DuckDB failed" in result.stderr
    assert not Path(f"{sandbox.database}.stage").exists()
    assert not sandbox.database.exists()


@needs_duckdb
def test_database_is_private_and_the_users_duckdbrc_never_runs(sandbox: Sandbox) -> None:
    _ = (sandbox.root / ".duckdbrc").write_text(
        ".bail on\nSELECT * FROM missing_table;\n.print not-json\n"
    )
    _seed_claude(sandbox)
    built = sandbox.run("ingest", "--force")
    assert built.returncode == 0, built.stderr
    assert stat.S_IMODE(sandbox.database.stat().st_mode) == 0o600
    result = sandbox.run("sql", "SELECT count(*) AS n FROM skill_invocations")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [{"n": 1}]



def _wait_until_blocked(process: subprocess.Popen[str], lock_path: Path) -> None:
    """Poll /proc/locks until `process` waits on the lock file's inode.

    A blocked flock waiter shows as a `->` line that names its pid and the inode.
    """
    inode = lock_path.stat().st_ino
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert process.poll() is None, "ingest exited instead of waiting for the lock"
        for line in Path("/proc/locks").read_text().splitlines():
            fields = line.split()
            if "->" in fields and fields[-4] == str(process.pid):
                if fields[-3].endswith(f":{inode}"):
                    return
        time.sleep(0.02)
    pytest.fail("ingest never waited on the lock")


def _start_ingest(sandbox: Sandbox) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "session_analytics", "ingest"],
        env=sandbox.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


needs_proc_locks = pytest.mark.skipif(
    not Path("/proc/locks").exists(), reason="the lock check reads /proc/locks"
)


@needs_duckdb
@needs_proc_locks
def test_ingest_waits_for_the_lock_and_then_succeeds(sandbox: Sandbox) -> None:
    _seed_claude(sandbox)
    lock_path = Path(f"{sandbox.database}.lock")
    lock_path.parent.mkdir(parents=True)
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)
    process = _start_ingest(sandbox)
    try:
        _wait_until_blocked(process, lock_path)
        assert not sandbox.database.exists()
        assert not Path(f"{sandbox.database}.stage").exists()
    except BaseException:
        process.kill()
        raise
    finally:
        os.close(holder)
    stdout, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr
    assert _json(stdout)["status"] == "ingested"


@needs_duckdb
@needs_proc_locks
def test_second_lock_waiter_finds_the_database_fresh_and_reports_cached(
    sandbox: Sandbox,
) -> None:
    _seed_claude(sandbox)
    lock_path = Path(f"{sandbox.database}.lock")
    lock_path.parent.mkdir(parents=True)
    holder = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    waiters: list[subprocess.Popen[str]] = []
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        waiters = [_start_ingest(sandbox), _start_ingest(sandbox)]
        for waiter in waiters:
            _wait_until_blocked(waiter, lock_path)
        assert not sandbox.database.exists()
    finally:
        os.close(holder)
        if sys.exc_info()[0] is not None:
            for waiter in waiters:
                waiter.kill()
    statuses: list[object] = []
    for waiter in waiters:
        stdout, stderr = waiter.communicate(timeout=120)
        assert waiter.returncode == 0, stderr
        statuses.append(_json(stdout)["status"])
    assert sorted(str(status) for status in statuses) == ["cached", "ingested"]

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

@needs_duckdb
def test_sessions_db_override_with_an_old_mtime_is_queried_without_a_refresh(
    sandbox: Sandbox,
) -> None:
    override = sandbox.root / "custom.duckdb"
    env = {"SESSIONS_DB": str(override)}
    _seed_claude(sandbox)
    assert sandbox.run("ingest", extra_env=env).returncode == 0
    os.utime(override, (0, 0))
    result = sandbox.run("sql", "SELECT version FROM schema_info", extra_env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [{"version": 1}]
    assert override.stat().st_mtime == 0
    assert not sandbox.database.exists()



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
