"""Ingest multi-harness coding-agent session logs into one DuckDB database.

Each harness has a *normalizing adapter* (see ``_adapters``) that discovers its
native session logs and emits one canonical row shape — the same JSON envelope
Claude already uses (type / message.content[] / timestamp / sessionId / cwd / …)
plus a ``harness`` tag. The adapters write their canonical rows to a staging
dir; the flattening SQL (see ``_tables``) then loads the union and threads
``harness`` through every session-scoped table, so a single query can compare
sources.

Adapters are discovery-gated and best-effort: a harness with no accessible logs
records a "no accessible logs" finding (see references/harness-coverage.md) and
is skipped non-fatally. "Done" is full coverage of what is reachable, not
parsing the unparseable.

Engine: DuckDB CLI — no Python duckdb module required. Skips ingestion if the
database is less than 1 hour old and carries the current schema version.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import time
from contextlib import ExitStack
from typing import cast

from agent_session_analytics._adapters import ADAPTERS
from agent_session_analytics._db import (
    DB_DIR,
    DB_PATH,
    DB_TMP_PATH,
    SCHEMA_VERSION,
    STAGE_DIR,
    IngestError,
    db_is_fresh,
    query_rows,
    require_duckdb,
    validate_settings,
)
from agent_session_analytics._emitters import DERIVED_TABLES
from agent_session_analytics._tables import build_tables, derived_path


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def stage_harnesses() -> list[str]:
    """Run every adapter; write canonical rows to per-harness staging JSONL.

    The same pass writes narrow pre-flattened rows for each derived table
    (see _emitters). Returns the list of harness names that produced at least
    one row. Adapters that discover no logs (or have no normalizer) are
    reported and skipped.
    """
    if os.path.isdir(STAGE_DIR):
        shutil.rmtree(STAGE_DIR)
    os.makedirs(os.path.join(STAGE_DIR, "derived"), exist_ok=True)

    loaded: list[str] = []
    with ExitStack() as stack:
        sinks = [
            (table, stack.enter_context(open(derived_path(table.name), "w", encoding="utf-8")))
            for table in DERIVED_TABLES
        ]
        for name, discover, normalize in ADAPTERS:
            sources = discover()
            if not sources or normalize is None:
                _log(f"  {name}: no accessible logs — skipped")
                continue
            out_path = os.path.join(STAGE_DIR, f"{name}.jsonl")
            count = 0
            with open(out_path, "w", encoding="utf-8") as out:
                for src in sources:
                    emitters = [(table.make_emitter(name, src), sink) for table, sink in sinks]
                    for entry in normalize(src):
                        _ = out.write(json.dumps(entry) + "\n")
                        count += 1
                        for emit, sink in emitters:
                            for row in emit(cast(dict[str, object], entry)):
                                _ = sink.write(json.dumps(row) + "\n")
            if count:
                loaded.append(name)
                _log(f"  {name}: {count} canonical entries")
            else:
                os.remove(out_path)
                _log(f"  {name}: no accessible logs — skipped")
    return loaded


def _do_ingest_and_swap() -> float:
    t0 = time.monotonic()  # The build time includes the stage step.
    _log("Discovering + normalizing harness sessions...")
    loaded = stage_harnesses()
    if not loaded:
        raise IngestError("No accessible sessions from any harness. Nothing to ingest.")

    if os.path.exists(DB_TMP_PATH):
        if os.path.isdir(DB_TMP_PATH):
            raise IngestError(f"{DB_TMP_PATH} is a directory, expected a file.")
        os.remove(DB_TMP_PATH)

    _log("Loading canonical rows into DuckDB...")
    build_tables(_log)

    os.replace(DB_TMP_PATH, DB_PATH)
    return time.monotonic() - t0


def _summary() -> dict[str, object]:
    counts = query_rows(
        """
        SELECT
            (SELECT count(*) FROM tool_uses) AS tool_uses,
            (SELECT count(*) FROM tool_results) AS tool_results,
            (SELECT count(DISTINCT harness) FROM tool_uses) AS harnesses,
            (SELECT count(*) FROM agent_spawns) AS agent_spawns,
            (SELECT count(*) FROM skill_invocations) AS skill_invocations,
            (SELECT count(*) FROM mcp_calls) AS mcp_calls,
            (SELECT count(*) FROM sessions) AS sessions,
            (SELECT count(*) FROM permission_denials) AS permission_denials;
    """
    )
    # Coverage (issue #704): measurement quality per harness, so consumers can
    # see how much of the call volume has joined results and how many error
    # flags were explicit in the source vs backfilled. A low results_joined_pct
    # means per-tool error rates for that harness are floors.
    coverage = query_rows(
        """
        WITH result_ids AS (SELECT DISTINCT tool_use_id FROM tool_results)
        SELECT
            u.harness,
            count(*) AS tool_uses,
            round(100.0 * avg(CASE WHEN r.tool_use_id IS NOT NULL THEN 1 ELSE 0 END), 1)
                AS results_joined_pct,
            (SELECT count(*) FROM tool_results tr WHERE tr.harness = u.harness)
                AS tool_results,
            (SELECT round(100.0 * avg(CASE WHEN tr.is_error_explicit THEN 1 ELSE 0 END), 1)
               FROM tool_results tr WHERE tr.harness = u.harness)
                AS explicit_error_flag_pct
        FROM tool_uses u
        LEFT JOIN result_ids r ON u.tool_use_id = r.tool_use_id
        GROUP BY u.harness
        ORDER BY u.harness;
    """
    )
    return {"counts": counts[0] if counts else {}, "coverage": coverage}


def build_database(*, force: bool = False) -> float | None:
    """Build or refresh the database; return the build seconds, or None when it is cached.

    A database that is newer than the TTL and carries the current schema
    version is left alone unless ``force`` is set. An `IngestError` reports
    every expected failure, including a failed directory, lock, or stage step.
    """
    _ = os.umask(0o077)
    require_duckdb()
    validate_settings()  # Reject a bad environment value before the slow stage step.

    if not force and db_is_fresh():
        return None

    try:
        os.makedirs(DB_DIR, exist_ok=True)
        lock_fd = os.open(f"{DB_PATH}.lock", os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            # A waiting run checks freshness again: the lock holder may have just rebuilt.
            if not force and db_is_fresh():
                return None
            try:
                return _do_ingest_and_swap()
            finally:
                shutil.rmtree(STAGE_DIR, ignore_errors=True)
        finally:
            os.close(lock_fd)
    except OSError as error:
        raise IngestError(f"Cannot build the session database: {error}") from error


def ingest(force: bool = False) -> dict[str, object]:
    """Build or refresh the database; return a JSON-ready summary."""
    elapsed = build_database(force=force)
    if elapsed is None:
        return _cached()
    return {
        "status": "ingested",
        "database": DB_PATH,
        "schema_version": SCHEMA_VERSION,
        "elapsed_seconds": round(elapsed, 1),
        **_summary(),
    }


def _cached() -> dict[str, object]:
    age_minutes = (time.time() - os.path.getmtime(DB_PATH)) / 60
    return {
        "status": "cached",
        "database": DB_PATH,
        "schema_version": SCHEMA_VERSION,
        "age_minutes": round(age_minutes),
        "note": "database is newer than the one-hour cache; pass --force to re-ingest",
    }
