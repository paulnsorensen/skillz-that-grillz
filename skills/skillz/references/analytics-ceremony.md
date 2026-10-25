# Analytics ceremony (`audit` and `improve`)

Empirical usage data is best-effort. This skill bundles `scripts/agent-session-analytics.pyz`;
it does not need an installed `session-analytics` skill. The CLI shares that
skill's database path and one-hour freshness window. It requires Python 3, the
`duckdb` CLI, readable local session logs, and a writable database directory.
If DuckDB is absent (exit 3), stop this ceremony. The `audit` mode in `SKILL.md`
owns the Skip Usage and raw-log choice. If the user skips, or if ingestion or
another prerequisite fails, omit Usage, state why, and continue the audit.

Ingestion writes the analytics cache. It does not modify the target. The packs
run read-only.

1. Resolve this installed `skillz` directory from its loaded `SKILL.md` path.
2. Resolve the database path with
   `python3 <abs-skillz>/scripts/agent-session-analytics.pyz db-path`. The command
   prints `{"path": "<abs path>"}`. Use the `path` field as `<abs-database>`.
   `SESSIONS_DB` overrides it. Otherwise, an absolute `XDG_CACHE_HOME` selects
   `<cache>/agent-session-analytics/sessions.duckdb`; a relative or unset
   value uses `~/.cache/agent-session-analytics/sessions.duckdb`. If that file is
   absent and the legacy `<cache>/dotfiles/session-analytics/sessions.duckdb`
   exists, the command prints the legacy path. A relative
   `SESSIONS_DB` resolves against the current directory. Ingest also writes the
   transient `<db>.tmp` and `<db>.stage`, and a persistent `<db>.lock`.
3. Run `python3 <abs-skillz>/scripts/agent-session-analytics.pyz ingest` before
   querying. A database inside the freshness window makes ingest a no-op.
   Exit 3 means DuckDB is missing: stop this ceremony. Exit 4 means ingestion
   failed or timed out: omit Usage. The `sql` command also refreshes an old or
   absent default database, but not a `SESSIONS_DB` override.
4. Run **one pack per fresh read-only context**, in parallel, so raw query
   output never lands in the audit's window. Pass absolute paths for every
   input; the runner applies no defaults. Each context returns one ~2 KB digest.
   Do not collapse the packs into one all-domains run. Claude Code `Agent`,
   Codex `spawn_agent`, and OMP `task` are example hosts. Without sub-agents,
   run each pack's SQL with `python3 <abs-skillz>/scripts/agent-session-analytics.pyz sql "<sql>"`
   and keep only the digest. Exit 5 means DuckDB rejects the query.

   ```text
   Run analytics pack <abs-pack> for target <name> target_kind=<skill|agent>.
   harness=all pack=<abs-pack> schema=<abs-schema> conventions=<abs-conventions>
   coverage=<abs-coverage> database=<abs-database>
   ```

   Set `target_kind` from the shared protocol's **Read and classify** result.
   Each pack selects its start table from `target_kind`. The packs are the files
   that `SKILL.md` lists as the analytics packs. `SKILL.md` maps `schema`,
   `conventions`, and `coverage` to their data references.

   | Pack | Reveals |
   |---|---|
   | Usage | invocations, trend, project spread, peer rank |
   | Orchestration | tools, spawns, and MCP calls near the target |
   | Drift | declining usage, error-rate trend, new error signatures |

5. Carry the digests into the Usage lens.

`skill_invocations` and `agent_spawns` are Claude-dominant. Codex and OMP lack
hook and permission-denial rows. Cursor has no tool results. Before
cross-harness comparisons, read the harness-coverage reference that `SKILL.md`
lists.
