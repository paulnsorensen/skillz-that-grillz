---
status: accepted
owner: skillz
last_verified: 2026-10-11
confidence: high
sources:
  - skills/skillz/references/analytics-ceremony.md
  - packages/session-analytics/src/session_analytics/_ingest.py
  - dotfiles@6ce36879:skills/session-analytics/
---
# skillz analytics engine decisions

The analytics engine is the `session-analytics` package in
`packages/session-analytics/`. This repository is the only owner of the schema.
The package ships to PyPI. `skillz` also bundles it as
`skills/skillz/scripts/session-analytics.pyz`, built by `wedge` from the
`session-analytics` target in `skills/skillz/wedge.toml`.

The package merges two engines. One is the engine that `skillz` bundled from
`dotfiles` commit `f6f0cbf5`. The other is the `dotfiles` engine at commit
`6ce36879`. The fork point is now `6ce36879`. Every local delta below is kept.

`dotfiles` consumes the latest PyPI release, unpinned. It decorates the package
with its own reports and docs. The package ships engine primitives only: `ingest`,
`db-path`, `sql`, and `schema`. It has no canned reports.

The package keeps the shared `dotfiles/session-analytics/sessions.duckdb` cache
path, the `SESSIONS_DB` override, and the one-hour ingestion cache. The database
stores a schema version in the one-row `schema_info` table. A database with
another version is stale. `ingest` rebuilds it inside the cache window.

The Usage lens needs the DuckDB CLI and readable session logs. The package does
not need the Python DuckDB module. It never downloads DuckDB.

Exit codes:

- 3: the DuckDB CLI is missing. The message names four install commands.
- 4: ingest failed, the database is missing, or the schema version differs.
- 5: the query was rejected.

When DuckDB is absent, `skillz` asks one question with three choices. The user
can install DuckDB. The user can approve a slower, sampled raw-log scan by cheap
read-only models. The user can skip Usage. The scan needs consent and is not
comparable to database packs. No choice blocks the audit. `dotfiles` gets no scan.

The three `skillz` analytics packs take absolute paths to the schema, the
conventions, and the coverage notes. `skills/skillz/references/` holds copies of
these three files. A test fails when a copy differs from the package data.

## Local deltas from f6f0cbf5

The package carries these local fixes. They are not in the `dotfiles` engine.

- Every query path opens DuckDB read-only, so parallel packs do not collide.
- An `fcntl` lock serializes ingest runs. A waiting run checks freshness again.
- Ingest uses umask `077` for staged transcripts and the database.
- Ingest removes the stage directory after every run.
- Ingest and `sql` exit 3 with a clear message when DuckDB is absent.
- `sql` exits non-zero and shows the DuckDB error when a query fails.
- `sql` does not query a stale database after automatic ingestion fails.
- A `SESSIONS_DB` override never refreshes. A missing or mismatched database exits 4.
- `SESSIONS_DB` resolution does not expand `~` in a set value.
- `XDG_CACHE_HOME` resolution ignores a value that is not absolute.
- The adapter log-root variables follow the `SESSIONS_DB` rule by choice.
- The built-in log-root defaults (`~/.claude`, `~/.codex`, `~/.cursor`) expand `~` to `HOME`.
- Ingest stops when `DB_TMP_PATH` is a directory and does not delete it.

The `-init /dev/null` flag makes ingest ignore `~/.duckdbrc`. Both engines
had this flag. It was never a local-only delta.