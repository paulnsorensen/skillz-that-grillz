"""Run one read-only query against the session database."""

from __future__ import annotations

import os

from session_analytics._ingest import (
    DB_PATH,
    EXIT_QUERY,
    SCHEMA_VERSION,
    IngestError,
    db_is_fresh,
    db_is_stamped,
    query_rows,
    require_duckdb,
)
from session_analytics._ingest import ingest as run_ingest


def _refresh_default_database() -> None:
    """Ingest an old, absent, or wrong-version default database before the query."""
    if db_is_fresh():
        return
    try:
        _ = run_ingest()
    except IngestError as error:
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
    schema mismatch is an error, so the caller runs ``ingest`` on purpose.
    """
    require_duckdb()
    if os.environ.get("SESSIONS_DB"):
        if not os.path.exists(DB_PATH):
            raise IngestError(f"No session database at {DB_PATH}; run `session-analytics ingest`.")
        if not db_is_stamped():
            raise IngestError(
                f"The database at {DB_PATH} is not at schema version "
                + f"{SCHEMA_VERSION}; run `session-analytics ingest --force`."
            )
    else:
        _refresh_default_database()
    try:
        return query_rows(query)
    except IngestError as error:
        raise IngestError(str(error), exit_code=EXIT_QUERY) from error
