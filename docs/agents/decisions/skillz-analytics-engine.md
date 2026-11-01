---
status: accepted
owner: skillz
last_verified: 2026-10-11
confidence: high
sources:
  - skills/skillz/references/analytics-ceremony.md
  - packages/agent-session-analytics/src/agent_session_analytics/_ingest.py
  - dotfiles@6ce36879:skills/session-analytics/
---
# skillz analytics engine decisions

The analytics engine is the `agent-session-analytics` package in
`packages/agent-session-analytics/`. This repository is the only owner of the schema.
The package ships to PyPI. `skillz` also bundles it as
`skills/skillz/scripts/agent-session-analytics.pyz`, built by `wedge` from the
`agent-session-analytics` target in `skills/skillz/wedge.toml`.

The package merges two engines. One is the engine that `skillz` bundled from
`dotfiles` commit `f6f0cbf5`. The other is the `dotfiles` engine at commit
`6ce36879`. The fork point is now `6ce36879`. The package keeps every local delta below.

`dotfiles` consumes the latest PyPI release, unpinned. It decorates the package
with its own reports and docs. The package ships engine primitives only: `ingest`,
`db-path`, `sql`, and `schema`. It has no canned reports.

The package defaults to the `<cache>/agent-session-analytics/sessions.duckdb` cache
path. A public default must not carry a personal `dotfiles` name. If that file is
absent and the legacy `<cache>/dotfiles/session-analytics/sessions.duckdb` exists,
the package uses the legacy file. It never moves, copies, or deletes the legacy
file, because another tool can share it. The package keeps the `SESSIONS_DB`
override and the one-hour freshness window. The database
stores a schema version in the one-row `schema_info` table. A database with
another version is stale. `ingest` rebuilds it inside the freshness window.

The Usage lens needs the DuckDB CLI and readable session logs. The package does
not need the Python DuckDB module. It never downloads DuckDB.

Exit codes:

- 3: the DuckDB CLI is missing. The message names four install commands.
- 4: ingest failed or timed out, the database is missing, or the schema version differs.
- 5: DuckDB rejects or times out the query.

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
- Ingest passes `-init /dev/null`, so it ignores `~/.duckdbrc`. Only the upstream `query.sh` had this flag.
- Ingest adds an `anchor_id` column to `agent_spawns` and `skill_invocations`. The `dotfiles` `ingest.py` lacks it.
- `sql` runs in DuckDB safe mode, which rejects shell commands, file writes, and outside file reads.
