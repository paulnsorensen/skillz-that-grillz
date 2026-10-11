"""The session database: its paths, exit codes, errors, DuckDB runner, and freshness checks."""

from __future__ import annotations

import atexit
import functools
import json
import os
import re
import shutil
import subprocess
import tempfile
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
# DuckDB spills under this root. Its own default, `<database>.tmp`, collides
# with DB_TMP_PATH for queries and lands in the working directory for none.
# Each process spills to its own subdirectory (see spill_dir), because DuckDB
# deletes the spill files it finds in its temp directory when it closes.
SPILL_ROOT = f"{DB_PATH}.spill"
TTL_SECONDS = 3600  # 1 hour

# The schema version is an integer stamped in the one-row `schema_info` table.
# Raise it on every change to a table or column; a database with another
# version is stale, so ingest rebuilds it even inside the freshness window.
SCHEMA_VERSION = 2

EXIT_NO_DUCKDB = 3
EXIT_INGEST = 4
EXIT_QUERY = 5

DUCKDB_TIMEOUT_SECONDS = 600

# `memory_limit` bounds the buffer manager, not the process RSS. Fewer threads
# and unordered inserts keep the memory outside the buffer manager small.
MEMORY_LIMIT_ENV = "SESSIONS_DUCKDB_MEMORY_LIMIT"
THREADS_ENV = "SESSIONS_DUCKDB_THREADS"
DEFAULT_MEMORY_LIMIT = "4GB"
DEFAULT_THREADS = 4
MAX_THREADS = 256
MAX_SPILL_SIZE = "20GB"
_SIZE = re.compile(r"(?i)(\d+(?:\.\d+)?) ?([KMGT])(i?)B")
# DuckDB cannot run a simple aggregate below 4MiB; 16MiB leaves headroom.
MIN_MEMORY_BYTES = 16 * 1024**2
# DuckDB stores the limit in 64 bits and refuses a larger value.
MEMORY_BYTES_CEILING = 2**64

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


def sql_quote(text: str) -> str:
    """Escape text for embedding as a single-quoted SQL literal."""
    return text.replace("'", "''")


def _memory_limit() -> str:
    value = os.environ.get(MEMORY_LIMIT_ENV) or DEFAULT_MEMORY_LIMIT
    match = _SIZE.fullmatch(value)
    if match:
        base = 1024 if match[3] else 1000
        multiplier = cast(int, base ** ("KMGT".index(cast(str, match[2]).upper()) + 1))
        size = int(float(cast(str, match[1])) * multiplier)
        if MIN_MEMORY_BYTES <= size < MEMORY_BYTES_CEILING:
            return value
    raise DatabaseError(
        f"{MEMORY_LIMIT_ENV} must be a size of at least 16MiB and below 16EiB, "
        + f"with a unit such as 4GB or 512MiB; got {value!r}."
    )


def _threads() -> int:
    value = os.environ.get(THREADS_ENV) or str(DEFAULT_THREADS)
    if not (value.isascii() and value.isdecimal() and 1 <= int(value) <= MAX_THREADS):
        raise DatabaseError(f"{THREADS_ENV} must be an integer from 1 to {MAX_THREADS}; got {value!r}.")
    return int(value)


def validate_settings() -> None:
    """Raise DatabaseError (exit 4) when a SESSIONS_DUCKDB_* environment value is invalid."""
    _ = _memory_limit(), _threads()


@functools.cache
def spill_dir() -> str:
    """This process's own spill directory under SPILL_ROOT, made on first use and removed at exit."""
    try:
        os.makedirs(SPILL_ROOT, mode=0o700, exist_ok=True)
        path = tempfile.mkdtemp(prefix=f"{os.getpid()}-", dir=SPILL_ROOT)
    except OSError as error:
        raise DatabaseError(f"Cannot create the DuckDB spill directory: {error}") from error
    _ = atexit.register(shutil.rmtree, path, True)
    return path


def duckdb_settings(*, ingest: bool) -> list[str]:
    """The SET statements a DuckDB run applies; an invalid environment value raises.

    Only ingest drops insertion order to save memory, so a read-only query
    keeps its row order.
    """
    return [
        f"SET memory_limit='{_memory_limit()}'",
        f"SET threads={_threads()}",
        *(["SET preserve_insertion_order=false"] if ingest else []),
        f"SET temp_directory='{sql_quote(spill_dir())}'",
        f"SET max_temp_directory_size='{MAX_SPILL_SIZE}'",
    ]


def duckdb_argv(sql: str, *, as_json: bool = False) -> list[str]:
    """The DuckDB CLI arguments for one run.

    The JSON mode reads the database file in read-only safe mode. Safe mode locks
    the configuration, so every setting applies first. The other mode writes the
    temporary database that ingest builds.
    """
    argv = ["duckdb", "-init", "/dev/null"]
    if as_json:
        argv += ["-readonly", "-json"]
    argv.append(DB_PATH if as_json else DB_TMP_PATH)
    for statement in duckdb_settings(ingest=not as_json):
        argv += ["-cmd", statement]
    if as_json:
        argv += ["-cmd", ".safe_mode"]
    return argv + ["-c", sql]


def _duckdb(sql: str, *, as_json: bool = False) -> str:
    """Run SQL in the DuckDB CLI with the arguments from `duckdb_argv`."""
    argv = duckdb_argv(sql, as_json=as_json)
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
