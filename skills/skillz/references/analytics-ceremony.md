# Analytics ceremony (`audit` and `improve`)

Empirical usage data is best-effort. This skill bundles `scripts/session-analytics.pyz`;
it does not need an installed `session-analytics` skill. The CLI shares that
skill's database path and one-hour cache. It requires Python 3, the `duckdb`
CLI, readable local session logs, and a writable database directory.
If DuckDB is absent (exit 3), stop this ceremony. The `audit` mode in `SKILL.md`
owns the Skip Usage and raw-log choice. If the user skips, or if ingestion or
another prerequisite fails, omit Usage, state why, and continue the audit.

Ingestion writes the analytics cache. It does not modify the target. The packs
run read-only.

1. Resolve this installed `skillz` directory from its loaded `SKILL.md` path.
2. Resolve the database path with
   `python3 <abs-skillz>/scripts/session-analytics.pyz db-path`. `SESSIONS_DB`
   overrides it. Otherwise, an absolute `XDG_CACHE_HOME` selects
   `<cache>/dotfiles/session-analytics/sessions.duckdb`; a relative or unset
   value uses `~/.cache/dotfiles/session-analytics/sessions.duckdb`. A relative
   `SESSIONS_DB` resolves against the current directory. Ingest also writes the
   transient `<db>.tmp` and `<db>.stage`, and a persistent `<db>.lock`.
3. Run `python3 <abs-skillz>/scripts/session-analytics.pyz ingest` before
   querying. The one-hour cache makes a fresh database a no-op. Exit 3 means
   DuckDB is missing: stop this ceremony. Exit 4 means ingestion failed: omit
   Usage. The `sql` command also refreshes an old or absent default database,
   but not a `SESSIONS_DB` override.
4. Query with `python3 <abs-skillz>/scripts/session-analytics.pyz sql "<sql>"`.
   Exit 5 means the query was rejected.
5. Run **one pack per fresh read-only context**, in parallel, so raw query
   output never lands in the audit's window. Pass absolute paths for every
   input; the runner applies no defaults. Each context returns one ~2 KB digest.
   Do not collapse the packs into one all-domains run. Claude Code `Agent`,
   Codex `spawn_agent`, and OMP `task` are example hosts. Without sub-agents,
5. Run **one pack per fresh read-only context**, in parallel, so raw query
   output never lands in the audit's window. Pass absolute paths for every
   input; the runner applies no defaults. Each context returns one ~2 KB digest.
   Do not collapse the packs into one all-domains run. Claude Code `Agent`,
   Codex `spawn_agent`, and OMP `task` are example hosts. Without sub-agents,
   run each pack's SQL with `python3 <abs-skillz>/scripts/session-analytics.pyz sql "<sql>"`
   and keep only the digest.

   ```text
   Run analytics pack <abs-pack> for target <name> target_kind=<skill|agent>.
   harness=all pack=<abs-pack> schema=<abs-schema> conventions=<abs-conventions>
   coverage=<abs-coverage> database=<abs-database>
   ```

   Set `target_kind` from the shared protocol's **Read and classify** result.
   Each pack selects its start table from `target_kind`. The packs are the files
   that `SKILL.md` lists as the analytics packs.

   | Pack | Reveals |
   |---|---|
   | Usage | invocations, trend, project spread, peer rank |
   | Orchestration | tools, spawns, and MCP calls near the target |
   | Drift | declining usage, error-rate trend, new error signatures |

6. Carry the digests into the Usage lens.

The packs live in `references/`. The three data references (`canonical-schema`,
`harness-coverage`, and `query-conventions`) live in the same directory.
Pass their paths to each pack context as `schema`, `conventions`, and `coverage`.

`skill_invocations` and `agent_spawns` are Claude-dominant. Codex and OMP lack
hook and permission-denial rows. Cursor has no tool results. Read the
`coverage` input before cross-harness comparisons.
