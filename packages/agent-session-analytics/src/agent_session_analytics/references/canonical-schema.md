# Canonical Schema

Contents: [tool_uses](#tool_uses), [tool_results](#tool_results), [stop_events](#stop_events), [model_turns](#model_turns), [agent_spawns](#agent_spawns), [skill_invocations](#skill_invocations), [mcp_calls](#mcp_calls), [sessions](#sessions), [stop_hooks](#stop_hooks), [permission_denials](#permission_denials), [user_turns](#user_turns), [interrupt_events](#interrupt_events), [raw_entries](#raw_entries), [schema_info](#schema_info), [Type gotchas](#type-gotchas).

The shape every harness adapter normalizes into and every analytics pack queries
against. `agent-session-analytics ingest` loads one canonical row shape (the Claude envelope plus a
`harness` tag) into `${XDG_CACHE_HOME:-$HOME/.cache}/dotfiles/session-analytics/sessions.duckdb` by default, then flattens it into
the tables below. **Pack authors: write SQL against these tables; never reach
into a harness's native format.**

Every session-scoped table carries a `harness` column
(`claude`/`codex`/`omp`/`pi`/`cursor`/`copilot`). Filter or group by it to
compare sources; omit it to aggregate across all reachable harnesses.

## `tool_uses`

Flattened from assistant `message.content[]` blocks where `type='tool_use'`.

| Column | Type | Description |
|--------|------|-------------|
| harness | VARCHAR | Source harness |
| tool_name | VARCHAR | Tool invoked (Bash, Read, Edit, Agent, Skill, mcp__*, or a harness-native name like `shell`/`apply_patch`) |
| tool_use_id | VARCHAR | Unique ID for joining with `tool_results` |
| input | JSON | Full input object |
| bash_cmd | VARCHAR | Extracted command (Bash only) |
| skill_name | VARCHAR | Extracted skill (Skill only — claude) |
| skill_args | VARCHAR | Extracted args (Skill only — claude) |
| agent_type | VARCHAR | Extracted subagent_type (Agent only — claude) |
| agent_desc | VARCHAR | Extracted description (Agent only) |
| agent_mode | VARCHAR | Extracted mode (Agent only) |
| grep_pattern | VARCHAR | Extracted pattern (Grep only) |
| file_path | VARCHAR | Extracted file_path (Read/Edit/Write) |
| query | VARCHAR | Extracted query (ToolSearch) |
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |
| cwd | VARCHAR | Working directory |
| gitBranch | VARCHAR | Git branch (claude only) |

## `tool_results`

Flattened from user `message.content[]` blocks where `type='tool_result'`.

| Column | Type | Description |
|--------|------|-------------|
| harness | VARCHAR | Source harness |
| tool_use_id | VARCHAR | Matches `tool_uses.tool_use_id` |
| content | VARCHAR | Result text (truncated to 500 chars) |
| is_error | VARCHAR | `'true'` if the call failed (string, not boolean). Never NULL — an absent source flag backfills to `'false'` |
| is_error_explicit | BOOLEAN | Whether the source block carried the flag. `false` means `is_error` was backfilled (claude omits the flag on most successes) |
| timestamp | VARCHAR | ISO timestamp |
| sessionId | VARCHAR | Session identifier |

## `stop_events`

Assistant messages where the model stopped generating (claude, cursor, omp, and pi;
omp and pi quota stalls show as `error`). Columns: `harness`,
`stop_reason`, `timestamp`, `sessionId`, `cwd`, `gitBranch`.

## `model_turns`

One row per assistant turn that names a model. Use it for round-trip latency,
batching, and quota analysis. Claude splits one message
into several raw entries; this table groups them by `message.id`.

| Column | Type | Description |
|--------|------|-------------|
| harness | VARCHAR | Source harness (claude, codex, omp, and pi; cursor names no model; codex rows use the latest `turn_context` model, or `unknown` before the first one) |
| model | VARCHAR | Model id; omp and pi use `<provider>/<model>`, or the bare model when the log names no provider |
| stop_reason | VARCHAR | Canonical stop reason; omp and pi `toolUse`/`stop` map to `tool_use`/`end_turn` |
| error_message | VARCHAR | Provider error text on an `error` stop (omp and pi) |
| input_tokens / output_tokens / cache_read_tokens / cache_creation_tokens | BIGINT | Token usage for the turn. Codex rows come from `token_count` events: one row per event with a non-null `last_token_usage`, skipping an event whose `total_token_usage` repeats the previous one. Codex `input_tokens` includes cached tokens. Claude, omp, and pi `input_tokens` exclude cache reads and cache creation. omp and pi `cache_creation_tokens` come from `usage.cacheWrite`. Codex reports no `cache_creation_tokens` |
| context_tokens | BIGINT | One context-size measure for every harness. Claude, omp, and pi: `input_tokens + cache_read_tokens + cache_creation_tokens`. Other harnesses, including Codex: `input_tokens` |
| prompt_tokens | BIGINT | Full context size sent for the turn (omp and pi) |
| duration_ms / ttft_ms | DOUBLE | Model round-trip time and time to first token (omp and pi) |
| tool_calls | BIGINT | Tool calls issued in the turn; 1 means the turn did not batch. NULL for Codex: its token rows carry no content, so Codex tool calls and `stop_reason` are not measured |
| timestamp, sessionId, cwd | VARCHAR | Join keys |

## `agent_spawns`

Subset of `tool_uses` for `Agent` (claude) and `Task` (cursor) calls. Columns: `harness`,
`agent_type` (defaults to `general-purpose`), `description`, `mode`, `anchor_id`,
`timestamp`, `sessionId`, `cwd`. `anchor_id` is the spawn's `tool_use_id`.

## `skill_invocations`

Subset of `tool_uses` for `Skill` calls (claude). Columns: `harness`,
`skill_name`, `args`, `anchor_id`, `timestamp`, `sessionId`, `cwd`.
`anchor_id` is the invocation's `tool_use_id`. Window queries use it to exclude the anchor event.
`anchor_id` is NULL when the log names no tool call id.

## `mcp_calls`

Subset of `tool_uses` where `tool_name LIKE 'mcp__%'`. Same columns as
`tool_uses`. Names are harness-specific: Claude and Cursor use
`mcp__<server>__<method>`, while Pi and OMP preserve
`mcp__<server>_<method>`. Consumers must branch on `harness`; for Pi-family
rows, split the suffix once at the first underscore.

## `sessions`

One row per `(harness, sessionId, cwd, branch)`. Columns: `harness`,
`sessionId`, `first_seen`, `last_seen`, `project` (cwd), `branch`, `version`,
`parent_session_id`, `entry_count`. `version` is the harness CLI version (Claude `version`, Codex
`session_meta.cli_version`) of the latest entry by timestamp. It is NULL when
the log names none.
`entry_count` counts the rows of the session in `raw_entries` except Codex `token_usage` rows. Those rows are synthetic and feed only `model_turns`. Codex user prompts, `task_complete`, and `turn_aborted` rows count.
`parent_session_id` is the Codex `source.subagent.thread_spawn.parent_thread_id` of a sub-agent rollout. It is NULL for any other session.
A forked Codex sub-agent rollout counts as a standalone session with its own `sessionId`. A Claude sidechain keeps the parent `sessionId`.

## `stop_hooks`

System entries with subtype `stop_hook_summary` (claude only — codex/omp emit
none). Columns: `harness`, `timestamp`, `sessionId`, `hookCount`,
`hookInfos` (JSON), `hookErrors` (JSON), `preventedContinuation`, `stopReason`,
`hasOutput`, `level`.

## `permission_denials`

Pre-filtered `tool_results` for permission/hook denials (claude-dominant).
Columns: `harness`, `content`, `sessionId`, `timestamp`.

## `user_turns`

One row per real user prompt (claude, codex, omp, and pi; cursor is not covered).
The table holds the full prompt text. The database file is private to the user (umask 077).

| Column | Type | Description |
|--------|------|-------------|
| harness | VARCHAR | Source harness |
| sessionId | VARCHAR | Session identifier |
| timestamp | VARCHAR | ISO timestamp of the prompt |
| turn_index | BIGINT | 0-based position among the real prompts of the session, ordered by timestamp, then by source file and position in that file |
| text | VARCHAR | Full prompt text, not truncated. Text blocks join with a newline |
| prev_assistant_text | VARCHAR | Last assistant text before the prompt in the same session and source file, or NULL when none precedes it. Claude, omp, and pi: the joined text blocks of the last assistant entry with non-blank text (Claude skips sidechain entries). Codex: `last_agent_message` of the last `task_complete` event; NULL when that turn ended without one or was aborted (`turn_aborted`) |

A real Claude prompt is a `user` entry that is not `isMeta`, `isCompactSummary`, or `isSidechain`.
It has string content or a text block, and its text is not blank and does not start with `<` or `[Request interrupted`.
A real omp or pi prompt is a `user` row with non-blank string content or text blocks, and its `attribution` is not `agent`. Codex prompts come from `item_completed` `UserMessage` events with non-blank text. A Codex rollout whose `session_meta.source` has a `subagent` value (`raw_entries.isSidechain` true) gives no rows: an agent writes its prompts. This covers `thread_spawn` children and `other` or `review` runs, such as `guardian`, that name no parent.

## `interrupt_events`

A table of user interrupts from structured markers only. Columns: `harness`,
`sessionId`, `timestamp`. Claude: a main-session `user` entry (not `isSidechain`)
whose string content or first text block starts with `[Request interrupted`.
Codex: a `turn_aborted` event with reason `interrupted` in a rollout that is not
agent-run (`isSidechain` is not true). Cursor, omp, and pi aborts are not covered (see `stop_events`).

`interrupt_events` overlaps `permission_denials`. A rejected Claude tool prompt
writes a "doesn't want to proceed" tool result and a
`[Request interrupted by user for tool use]` marker, so one rejection can appear
in both. Do not add the two counts.

## `raw_entries`

The full unflattened canonical rows (post-adapter). Carries `harness` plus every
column in the ingest engine's `RAW_COLUMNS`. Use only when the materialized tables
lack a field you need.

## `schema_info`

One row with one integer column, `version`. It holds the schema version of the
database. `agent-session-analytics ingest` rebuilds a database whose version differs
from the package version, even inside the one-hour freshness window.

## Type gotchas

- `is_error` is VARCHAR `'true'`/`'false'` — compare as strings. It is never
  NULL: an absent source flag is backfilled to `'false'` (`is_error_explicit`
  records which rows were backfilled). Measured on claude logs: ~99.5% of
  absent-flag results carry non-error content, so the backfill is sound;
  claude error rates remain floors, not exact estimates.
- `is_error_explicit` is a real BOOLEAN — `-json` prints it bare
  (`true`/`false`), unlike the VARCHAR `is_error`.
- Timestamps are VARCHAR ISO strings — cast for date math (`timestamp::TIMESTAMP`,
  `timestamp::DATE`).
- DuckDB CLI `-json` preserves column types: BOOLEAN/BIGINT print bare, the
  schema's many VARCHAR columns print as strings.
- Empty result sets print `[]`.
- `input` is JSON — use `json_extract_string()` for fields not already
  materialized as columns.
