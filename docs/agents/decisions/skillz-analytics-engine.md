---
status: accepted
owner: skillz
last_verified: 2026-09-28
confidence: high
sources:
  - skills/skillz/references/analytics-ceremony.md
  - dotfiles@f6f0cbf5:skills/session-analytics/
---
# skillz analytics engine decisions

`skillz` bundles the analytics engine from `dotfiles` commit `f6f0cbf5`.
The source is `skills/session-analytics/scripts/` and its schema, coverage,
and query-conventions references. The bundled engine lives under
`skills/skillz/engine/`. It is internal to `skillz`, not a second published skill.

The engine preserves the shared
`dotfiles/session-analytics/sessions.duckdb` cache path, `SESSIONS_DB` override,
and one-hour ingestion cache. The database Usage lens needs the DuckDB CLI and
readable session logs. The engine does not need the Python DuckDB module or an
installed `session-analytics` skill. When DuckDB is absent, `skillz` offers a
slower, sampled raw-log scan through low-cost read-only subagents. User consent
is required. The scan may omit metrics and is not comparable to database packs.
The user can skip Usage instead. Neither path blocks the audit.

The three `skillz` analytics packs resolve their schema from the bundled
engine. Keep their paths local when updating the upstream snapshot.

## Local deltas from f6f0cbf5

The bundled engine carries these local fixes. They are not upstream yet.

- Every query path opens DuckDB read-only, so parallel packs do not collide.
- An `fcntl` lock serializes ingest runs. A waiting run checks freshness again.
- Ingest uses umask `077` for staged transcripts and the database.
- Ingest removes the stage directory after every run.
- Ingest and `query.sh` exit 3 with a clear message when DuckDB is absent.
- Python path resolution matches `db-path.sh` and does not expand `~`.
- Ingest passes `-init /dev/null`, so it ignores `~/.duckdbrc`.
- Ingest stops when `DB_TMP_PATH` is a directory and does not delete it.

These items stay with the upstream engine: a schema-version guard for the
shared database, incremental ingest, and removal of unused canned reports.
