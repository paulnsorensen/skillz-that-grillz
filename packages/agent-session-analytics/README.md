# agent-session-analytics

`agent-session-analytics` ingests the session logs of coding agents into one DuckDB
database. It then answers SQL queries that cannot write or run commands. It
supports the Claude Code, Codex, Cursor, OMP, and pi harnesses.

The package ships engine primitives only. It has no canned reports. A caller
writes its own SQL against the schema.

## Install

```bash
uv tool install agent-session-analytics
# or
pip install agent-session-analytics
```

The only runtime dependency is `fromargs`. The package calls the `duckdb`
command-line tool and does not need the Python `duckdb` module. Install DuckDB
with one of these commands:

```bash
uv tool install duckdb-cli
brew install duckdb
mise use -g duckdb
curl https://install.duckdb.org | sh
```

The package never downloads DuckDB. When `duckdb` is not on `PATH`, `ingest`
and `sql` exit 3 and print these options.

## Commands

Every command prints one JSON document on stdout. Every error is one JSON line
on stderr: `{"error": <message>, "exit_code": <n>}`.

| Command | Result |
|---|---|
| `ingest [--force]` | Build or refresh the database. A database inside the one-hour freshness window with the current schema version keeps its data. |
| `db-path` | The resolved database path, as `{"path": "<abs path>"}`. |
| `sql <query>` | The rows of one query in DuckDB safe mode. |
| `schema` | The canonical schema reference. |

```bash
agent-session-analytics ingest
agent-session-analytics db-path | jq -r .path
agent-session-analytics sql "SELECT harness, count(*) AS sessions FROM sessions GROUP BY harness"
```

`sql` opens the database read-only and runs in DuckDB safe mode. Safe mode
rejects shell commands, file writes, and reads of files outside the database.
A rejected query exits 5.

`sql` ingests an old or absent default database first. It does not ingest a
`SESSIONS_DB` override. It never queries stale data after a failed ingest.

A consumer that queries the database directly runs
`duckdb -init /dev/null -readonly <database> -json -c "<sql>"`, where
`<database>` is the `path` field of `db-path`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Bad input |
| 3 | The `duckdb` command is missing |
| 4 | Ingest failed or timed out, or the database is missing, unreadable, or has another schema version |
| 5 | DuckDB rejects the query, or the query times out |

## Database path

- `SESSIONS_DB` names the database. A relative value joins the current
  directory. The package does not expand `~` in a set value.
- Otherwise an absolute `XDG_CACHE_HOME` selects
  `<cache>/dotfiles/session-analytics/sessions.duckdb`. A relative or unset
  value uses `~/.cache`.
- `SESSIONS_DUCKDB_MEMORY_LIMIT` sets the DuckDB memory limit. The default is
  `8GB`.
- `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and `CURSOR_HOME` move the log roots.

Ingest writes the transient `<db>.tmp` and `<db>.stage`, and a persistent
`<db>.lock`. An `fcntl` lock serializes ingest runs. Ingest uses umask `077`.

## Schema version

The database holds a one-row `schema_info` table with an integer `version`.
The package version of that integer is `SCHEMA_VERSION`. `ingest` rebuilds a
database whose stored version differs, even inside the freshness window. `sql`
treats a mismatch like a stale database. Only this repository changes the
schema.

## References

These files ship as package data in `agent_session_analytics/references/`:

- `canonical-schema.md` — tables and columns. `agent-session-analytics schema` prints it.
- `harness-coverage.md` — what each harness log provides.
- `query-conventions.md` — rules for writing queries.
