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