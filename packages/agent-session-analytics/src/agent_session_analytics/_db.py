"""The session database: its paths, exit codes, errors, DuckDB runner, and freshness checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import TypeAlias

_JsonValue: TypeAlias = "None | bool | int | float | str | list[_JsonValue] | dict[str, _JsonValue]"


def configured_path(name: str, default: str) -> str:
    # SESSIONS_DB: a set value gets no `~` expansion, and a
    # relative value joins to cwd. The adapter log roots use the same rule by
    # choice. Only the built-in default expands `~` to HOME.
    value = os.environ.get(name) or os.path.expanduser(default)
    return os.path.abspath(value)


def _xdg_cache_home() -> str:
    # The XDG Base Directory spec requires ignoring a relative value.
    # No `~` expansion; only an absolute value counts.
    value = os.environ.get("XDG_CACHE_HOME")
    if value and value.startswith("/"):
        return os.path.abspath(value)
    return os.path.abspath(os.path.expanduser("~/.cache"))


DEFAULT_DB_DIR = os.path.join(_xdg_cache_home(), "dotfiles", "session-analytics")
DB_PATH = configured_path("SESSIONS_DB", os.path.join(DEFAULT_DB_DIR, "sessions.duckdb"))
DB_DIR = os.path.dirname(DB_PATH)
DB_TMP_PATH = f"{DB_PATH}.tmp"
# Derived from the database file name, not DB_DIR, so a SESSIONS_DB pointed
# into an existing directory never aliases (and rmtree's) that directory's
# own "stage" subdir.
STAGE_DIR = f"{DB_PATH}.stage"
TTL_SECONDS = 3600  # 1 hour

# The schema version is an integer stamped in the one-row `schema_info` table.
# Raise it on every change to a table or column; a database with another
# version is stale, so ingest rebuilds it even inside the freshness window.
SCHEMA_VERSION = 1

EXIT_NO_DUCKDB = 3
EXIT_INGEST = 4
EXIT_QUERY = 5

DUCKDB_TIMEOUT_SECONDS = 600

DUCKDB_MISSING = (
    "DuckDB CLI not found. Install it with one of: "
    "`uv tool install duckdb-cli`, `brew install duckdb`, `mise use -g duckdb`, "
    "or `curl https://install.duckdb.org | sh`. Nothing is downloaded automatically."
)


class DatabaseError(Exception):
    """A database step failed; ``exit_code`` is the CLI exit status."""

    def __init__(self, message: str, *, exit_code: int = EXIT_INGEST) -> None:
        super().__init__(message)
        self.exit_code: int = exit_code


class IngestError(DatabaseError):
    """An ingest step failed. The default exit status is 4."""


class QueryError(DatabaseError):
    """A query step failed. The default exit status is 5."""

    def __init__(self, message: str, *, exit_code: int = EXIT_QUERY) -> None:
        super().__init__(message, exit_code=exit_code)


def require_duckdb() -> None:
    if not shutil.which("duckdb"):
        raise DatabaseError(DUCKDB_MISSING, exit_code=EXIT_NO_DUCKDB)


def _duckdb(sql: str, *, as_json: bool = False) -> str:
    """Run SQL in the DuckDB CLI.

    The JSON mode reads the database file in read-only safe mode. Safe mode locks
    the configuration, so the memory limit applies first. The other mode writes
    the temporary database that ingest builds.
    """
    limit = os.environ.get("SESSIONS_DUCKDB_MEMORY_LIMIT") or "8GB"
    argv = ["duckdb", "-init", "/dev/null"]
    if as_json:
        argv += ["-readonly", "-json"]
    argv += [DB_PATH if as_json else DB_TMP_PATH, "-cmd", f"SET memory_limit='{limit}'"]
    if as_json:
        argv += ["-cmd", ".safe_mode"]
    argv += ["-c", sql]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=DUCKDB_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DatabaseError(f"DuckDB timed out after {DUCKDB_TIMEOUT_SECONDS} seconds.") from error
    if result.returncode != 0:
        raise DatabaseError(f"DuckDB failed: {result.stderr[:500].strip()}")
    return result.stdout.strip()


def run_sql(sql: str) -> None:
    _ = _duckdb(sql)


def query_rows(sql: str) -> list[dict[str, object]]:
    """Run one read-only query against the database and return its rows."""
    output = _duckdb(sql, as_json=True)
    if not output:
        return []
    try:
        rows = cast(_JsonValue, json.loads(output))
    except json.JSONDecodeError as error:
        raise DatabaseError(f"DuckDB returned unreadable output: {error}") from error
    if not isinstance(rows, list):
        raise DatabaseError("DuckDB returned a non-list result")
    return [dict(row) for row in rows if isinstance(row, dict)]


def stored_schema_version() -> int | None:
    """The schema version stamped in the database, or None when `schema_info` is missing.

    Every other DuckDB failure, such as a lock conflict or an invalid file, raises.
    """
    try:
        rows = query_rows("SELECT version FROM schema_info")
    except DatabaseError as error:
        if "Table with name schema_info does not exist" in str(error):
            return None
        raise
    version = rows[0]["version"] if rows else None
    return version if isinstance(version, int) else None


def db_is_stamped() -> bool:
    return os.path.exists(DB_PATH) and stored_schema_version() == SCHEMA_VERSION


def db_is_fresh() -> bool:
    if not os.path.exists(DB_PATH):
        return False
    age = time.time() - os.path.getmtime(DB_PATH)
    return age < TTL_SECONDS and stored_schema_version() == SCHEMA_VERSION
