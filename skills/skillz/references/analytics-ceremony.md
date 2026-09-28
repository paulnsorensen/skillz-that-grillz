# Analytics ceremony (`audit`, `self-update`)

Empirical usage data is best-effort. This skill bundles its analytics engine under
`engine/`; it does not need an installed `session-analytics` skill. The engine
shares that skill's database path and one-hour cache. It requires Python 3, the
`duckdb` CLI, readable local session logs, and a writable database directory.
It does not require the Python `duckdb` module. If DuckDB is absent, offer
**Skip Usage** or an opt-in sampled raw-log scan. Never start the scan before
the user chooses it. Follow `references/raw-log-fallback.md` only after consent.
If the user skips, or if ingestion or another prerequisite fails, omit Usage,
state why, and continue the audit.

1. Resolve this installed `skillz` directory from its loaded `SKILL.md` path.
2. Resolve the database path with `engine/scripts/db-path.sh` and its
   `sessions_db_path` function. `SESSIONS_DB` overrides it. Otherwise, an
   absolute `XDG_CACHE_HOME` selects
   `<cache>/dotfiles/session-analytics/sessions.duckdb`; a relative or unset
   value uses `~/.cache/dotfiles/session-analytics/sessions.duckdb`.
3. Run `python3 <abs-skillz>/engine/scripts/ingest.py` before querying. The
   one-hour cache makes a fresh database a no-op. If no logs are accessible or
   ingestion fails, omit Usage. `engine/scripts/query.sh` also auto-ingests an
   old or absent default database, but not a `SESSIONS_DB` override.
4. Run **one pack per fresh read-only context**, in parallel, so raw query
   output never lands in the audit's window. Pass absolute paths for every
   input; the runner applies no defaults. Each context returns one ~2 KB digest.
   Do not collapse the packs into one all-domains run. Claude Code `Agent`,
   Codex `spawn_agent`, and OMP `task` are example hosts. Without sub-agents,
   run each pack's SQL with `duckdb "<abs-database>" -json -c "<sql>"` and keep
   only the digest.

   ```text
   Run analytics pack <abs-pack> for target <name>. harness=all
   pack=<abs-pack> schema=<abs-schema> conventions=<abs-conventions>
   ingest=<abs-ingest> database=<abs-database>
   ```

   | Pack | Reveals |
   |---|---|
   | `skill-usage.md` | invocations, declared-vs-actual tool use, permission friction |
   | `agent-orchestration.md` | undeclared spawns, fork behavior, error rate |
   | `drift-regression.md` | declining usage, single-project concentration, hook interruptions |

5. Carry the digests into the Usage lens.

The packs live in `references/`. The bundled schema, conventions, coverage
notes, and ingest script live in `engine/references/` and `engine/scripts/`.
Pass those paths to each pack context. Where a pack names an installed
`session-analytics` skill, use this bundled engine instead.

`skill_invocations` and `agent_spawns` are Claude-dominant. Codex and OMP lack
hook and permission-denial rows. Cursor has no tool results. Read
`engine/references/harness-coverage.md` before cross-harness comparisons.
