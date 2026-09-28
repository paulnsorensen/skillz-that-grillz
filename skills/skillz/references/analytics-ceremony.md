# Analytics ceremony (`audit`, `self-update`)

Empirical usage data, best-effort.
The Usage lens reads the database that the `session-analytics` skill builds from coding-agent session logs; that skill is an optional sibling, not part of this package.
When that skill, the database, the logs, or ingestion is missing or fails, drop the Usage lens and say so in the report.
Never block the audit on it.

1. Resolve the installed `session-analytics` skill directory from its loaded `SKILL.md` path. When it is not installed, drop the Usage lens.
2. Compute the database path with the scripts' resolver: `$SESSIONS_DB` when set; else `$XDG_CACHE_HOME/dotfiles/session-analytics/sessions.duckdb` when `XDG_CACHE_HOME` is absolute; else `~/.cache/dotfiles/session-analytics/sessions.duckdb`.
3. Run **one pack per fresh read-only context**, in parallel, so raw query output never lands in the audit's window. Pass absolute paths for everything; the runner applies no defaults.

   Contract: fresh context, read-only, one pack, one ~2 KB digest in the pack's output format. Do not collapse to one all-domains run.
   Host syntax is an example, not the contract: Claude Code `Agent(subagent_type: "duckdb-expert", ...)`, Codex `spawn_agent`, OMP `task(...)`.
   A host without sub-agents runs each pack's SQL itself with `duckdb "<abs-database>" -json -c "<sql>"` and keeps only the digest.

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

4. Carry the digests into the Usage lens.

Contract paths, inside the installed `session-analytics` skill: schema `references/canonical-schema.md`, conventions `references/query-conventions.md`, ingest `scripts/ingest.py`.

Signal caveats: `skill_invocations` and `agent_spawns` are Claude-dominant; Codex and OMP lack hook and permission-denial rows; Cursor has no tool results. Read `references/harness-coverage.md` in that skill before quoting a cross-harness comparison.
