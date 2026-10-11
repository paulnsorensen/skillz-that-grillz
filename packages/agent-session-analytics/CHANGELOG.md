# Changelog

This file records the notable changes to the `agent-session-analytics` distribution.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
A `0.x` minor release can contain breaking changes.

The `publish agent-session-analytics` workflow reads the section for the tagged version.
It uses that section as the GitHub release notes.
To release, move the `Unreleased` entries into a new version section with the date.
Then set the same version in `packages/agent-session-analytics/pyproject.toml`.
Push a `agent-session-analytics-v<version>` tag after the release commit merges.

## [Unreleased]

### Changed

- Ingest builds `tool_uses`, `tool_results`, `stop_events`, and `model_turns` from narrow rows that the staging pass pre-flattens. DuckDB no longer runs `json_extract` over `raw_entries.message`. The `raw_entries` step runs on one thread. Peak memory of an ingest step drops from 11.1 GB to 2.6 GB on a 1 M-row log set. Tables, columns, and types stay the same.
- Every DuckDB run now sets `memory_limit` (default `4GB`, was `8GB`), `threads` (default `4`), `preserve_insertion_order=false`, and a 20GB spill cap.
- The schema version is now 2, so ingest rebuilds a version 1 database.
- The Codex adapter keeps `item_completed` `UserMessage` prompts, `task_complete`, and `turn_aborted` events as `raw_entries` rows. Codex `sessions.entry_count` counts them.
- The omp and pi adapters keep the `attribution` of a user row in `raw_entries.message`. A row with `attribution` `agent` is not a real prompt.

### Added

- `SESSIONS_DUCKDB_THREADS` sets the DuckDB thread count.
- DuckDB spills to the `<db>.spill` directory.
- A query-hygiene rule in `query-conventions.md`.
- The `user_turns` table: one row per real user prompt (claude, codex, omp, and pi) with `turn_index`, the full `text`, and `prev_assistant_text`.
- The `interrupt_events` view: Claude `[Request interrupted` markers and Codex `turn_aborted` events with reason `interrupted`.

## [0.1.0] - 2026-10-11

### Added

- The `agent-session-analytics` console script with four JSON commands: `ingest`, `db-path`, `sql`, and `schema`.
- A schema version stamped in the database. `ingest` rebuilds a database whose version differs, even inside the one-hour freshness window.
- The engine moves out of the `skillz` skill and merges the dotfiles `session-analytics` engine at dotfiles commit `6ce36879`.
- Codex `token_usage` rows reach `model_turns`. They do not count in `sessions.entry_count`.
- The columns `model_turns.cache_creation_tokens`, `model_turns.context_tokens`, `sessions.version`, and `sessions.parent_session_id`.
- The references `canonical-schema.md`, `harness-coverage.md`, and `query-conventions.md` ship as package data.

### Changed

- Codex `model_turns.tool_calls` is NULL, because Codex token rows carry no content.
- Cursor timestamps are UTC.

### Removed

- The canned `query.sh` reports. Callers write their own SQL with `sql`.

[Unreleased]: https://github.com/paulnsorensen/skillz-that-grillz/compare/agent-session-analytics-v0.1.0...HEAD
[0.1.0]: https://github.com/paulnsorensen/skillz-that-grillz/releases/tag/agent-session-analytics-v0.1.0
