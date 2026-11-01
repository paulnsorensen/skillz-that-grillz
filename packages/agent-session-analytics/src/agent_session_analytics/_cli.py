"""The `agent-session-analytics` command line: JSON in, JSON out."""

from __future__ import annotations

from importlib import resources

import fromargs

from agent_session_analytics._db import DB_PATH, DatabaseError

app = fromargs.App(
    "agent-session-analytics",
    help="Ingest coding-agent session logs into DuckDB and query them read-only.",
)

SCHEMA_REFERENCE = "canonical-schema.md"


def _cli_error(error: DatabaseError) -> fromargs.CliError:
    return fromargs.CliError(str(error), exit_code=error.exit_code)


@app.command
def ingest(*, force: bool = False) -> dict[str, object]:
    """Build or refresh the session database. A database under one hour old keeps its data."""
    from agent_session_analytics._ingest import ingest as run_ingest

    try:
        return run_ingest(force=force)
    except DatabaseError as error:
        raise _cli_error(error) from error


@app.command
def db_path() -> dict[str, str]:
    """Print the resolved database path. SESSIONS_DB and XDG_CACHE_HOME apply."""
    return {"path": DB_PATH}


@app.command
def sql(query: str) -> list[dict[str, object]]:
    """Run one read-only query and return its rows. The default database ingests itself when stale."""
    from agent_session_analytics._query import run_query

    try:
        return run_query(query)
    except DatabaseError as error:
        raise _cli_error(error) from error


@app.command
def schema() -> dict[str, str]:
    """Print the canonical schema reference that ships with the package."""
    reference = resources.files("agent_session_analytics").joinpath("references", SCHEMA_REFERENCE)
    return {"reference": SCHEMA_REFERENCE, "content": reference.read_text(encoding="utf-8")}


def main(argv: list[str] | None = None) -> int:
    return app.run(argv)
