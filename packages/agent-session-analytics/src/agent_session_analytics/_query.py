"""Run one read-only query against the session database."""

from __future__ import annotations

import os

from agent_session_analytics._db import (
    DB_PATH,
    SCHEMA_VERSION,
    DatabaseError,
    IngestError,
    QueryError,
    db_is_fresh,
    db_is_stamped,
    query_rows,
    require_duckdb,
    validate_settings,
)
from agent_session_analytics._ingest import build_database


def _refresh_default_database() -> None:
    """Rebuild an old, absent, or wrong-version default database before the query."""
    if db_is_fresh():
        return
    try:
        _ = build_database()
    except DatabaseError as error:
        outcome = (
            "refusing to query stale data"
            if os.path.exists(DB_PATH)
            else "no session database is available"
        )
        raise IngestError(
            f"Automatic ingestion failed ({error}); {outcome}.", exit_code=error.exit_code
        ) from error


def run_query(query: str) -> list[dict[str, object]]:
    """Return the rows of one read-only query.

    The default database refreshes itself when it is old or has another schema
    version. A ``SESSIONS_DB`` override never refreshes: a missing database or a
    schema mismatch is an error, so the caller runs ``ingest`` on purpose. An
    invalid ``SESSIONS_DUCKDB_*`` value is a query error (exit 5).
    """
    require_duckdb()
    try:
        validate_settings()
    except DatabaseError as error:
        raise QueryError(str(error)) from error
    if os.environ.get("SESSIONS_DB"):
        if not os.path.exists(DB_PATH):
            raise IngestError(f"No session database at {DB_PATH}; run `agent-session-analytics ingest`.")
        if not db_is_stamped():
            raise IngestError(
                f"The database at {DB_PATH} is not at schema version "
                + f"{SCHEMA_VERSION}; run `agent-session-analytics ingest --force`."
            )
    else:
        _refresh_default_database()
    try:
        return query_rows(query)
    except DatabaseError as error:
        raise QueryError(str(error)) from error
