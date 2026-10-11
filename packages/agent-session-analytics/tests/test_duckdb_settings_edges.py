"""Adversarial edge cases for the DuckDB memory_limit and threads environment validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from .conftest import Sandbox, claude_skill_entry, needs_duckdb

MEMORY = "SESSIONS_DUCKDB_MEMORY_LIMIT"
THREADS = "SESSIONS_DUCKDB_THREADS"


def _seed(sandbox: Sandbox) -> None:
    _ = sandbox.write_log("claude/projects/p/session.jsonl", [claude_skill_entry(sandbox.root)])


@needs_duckdb
@pytest.mark.parametrize(
    ("name", "value", "setting", "expected"),
    [
        (MEMORY, "1.5gb", "memory_limit", "1.3 GiB"),
        (MEMORY, "512 MiB", "memory_limit", "512.0 MiB"),
        (MEMORY, "1TB", "memory_limit", "931.3 GiB"),
        (THREADS, "1", "threads", 1),
        (THREADS, "256", "threads", 256),
        (THREADS, "08", "threads", 8),
    ],
)
def test_boundary_values_that_pass_validation_reach_duckdb(
    sandbox: Sandbox, name: str, value: str, setting: str, expected: object
) -> None:
    _seed(sandbox)
    env = {name: value}
    assert sandbox.run("ingest", "--force", extra_env=env).returncode == 0
    result = sandbox.run("sql", f"SELECT current_setting('{setting}') AS v", extra_env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [{"v": expected}]


@needs_duckdb
@pytest.mark.parametrize(
    ("name", "value"),
    [
        (THREADS, "４"),
        (THREADS, "+4"),
        (THREADS, " 4"),
        (THREADS, "4.0"),
        (MEMORY, "4"),
        (MEMORY, "4 G"),
        (MEMORY, "-1GB"),
        (MEMORY, "4GB "),
        (MEMORY, "0.0GiB"),
    ],
)
def test_malformed_values_fail_validation_before_the_stage_step(
    sandbox: Sandbox, name: str, value: str
) -> None:
    _seed(sandbox)
    result = sandbox.run("ingest", "--force", extra_env={name: value})
    assert result.returncode == 4, result.stderr
    error = str(cast(dict[str, object], json.loads(result.stderr))["error"])
    assert f"{name} must be" in error
    assert not Path(f"{sandbox.database}.stage").exists()


@needs_duckdb
@pytest.mark.xfail(strict=True, reason="press finding: the validator accepts memory limits DuckDB refuses")
@pytest.mark.parametrize(
    "value",
    [
        # DuckDB refuses the value: "Memory value out of range".
        "99999999999TB",
        # DuckDB rounds the value to 0 bytes, which the positive-size rule forbids.
        "0.0001KB",
    ],
)
def test_memory_limits_duckdb_refuses_fail_validation_before_the_stage_step(
    sandbox: Sandbox, value: str
) -> None:
    _seed(sandbox)
    result = sandbox.run("ingest", "--force", extra_env={MEMORY: value})
    assert result.returncode == 4, result.stderr
    # Validation runs before the stage step, so no stage log precedes the error.
    assert result.stderr.startswith("{"), result.stderr
    assert f"{MEMORY} must be" in result.stderr
    assert not sandbox.database.exists()


@needs_duckdb
def test_spill_directory_with_a_quote_in_the_database_path_is_escaped(sandbox: Sandbox) -> None:
    _seed(sandbox)
    database = sandbox.root / "it's here" / "sessions.duckdb"
    database.parent.mkdir()
    env = {"SESSIONS_DB": str(database)}
    built = sandbox.run("ingest", "--force", extra_env=env)
    assert built.returncode == 0, built.stderr
    result = sandbox.run("sql", "SELECT current_setting('temp_directory') AS spill", extra_env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [{"spill": f"{database}.spill"}]
