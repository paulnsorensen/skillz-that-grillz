# Query & Pack-Authoring Conventions

Conventions every analytics **pack** relies on. A pack is a skill-owned file at
`skills/<skill>/references/<domain>.md` describing one analytics domain. One
fresh read-only pack context runs exactly one pack, reading the *queries* from
the pack and the *schema* from `canonical-schema.md` (in this data layer).

## Pack file shape

```
# Pack: <domain>
# target_param: {TARGET}   — the placeholder queries substitute
# harness: <how the pack uses the harness filter>
# queries: ordered list of {name, sql}   — sql references the canonical schema
# output_format: a markdown template the digest fills

## 1. <query name>
```sql
SELECT ... FROM tool_uses WHERE ... ;
```

...

## Output Format

```
<markdown template — what the ~2 KB digest looks like>
```

```

Keep a pack to ~4-6 queries. The digest the agent returns must fit ~2 KB.

## Harness filtering

Every spawn carries a `harness=<all|claude|codex|omp|pi|cursor|copilot>`
parameter. In pack SQL:

- `harness='all'` → omit the harness predicate (aggregate every reachable source).
- a specific harness → add `WHERE harness = '<name>'` (or `AND harness = ...`).

State in the pack header which mode it expects. Domains that depend on
claude-only fields (`stop_hooks`, `permission_denials`, `skill_invocations`)
should say so and degrade to "insufficient signal" on other harnesses rather
than report zero as if it were meaningful. `agent_spawns` includes Claude
`Agent` and Cursor `Task`.

## Substitution

Queries use the placeholders that `target_param` and the pack header name
(e.g. `{TARGET}`, `{START_TABLE}`, `{NAME_COLUMN}`). The caller substitutes the
literal values before running. Quote the target as a string literal in SQL
(`WHERE skill_name = '{TARGET}'`).

## Empty results

If a query returns `[]`, note "no data" for that section and continue — never
block on one empty result. A pack that returns all-empty should say
"insufficient signal", not invent findings.

## Running queries

Resolve the database with `engine/scripts/db-path.sh` and its
`sessions_db_path` function. Then run each query through the CLI:

```bash
duckdb -readonly "<abs-database>" -json -c "SQL"
```

Ensure the database exists first with `python3 <abs-ingest>`. The one-hour
TTL skips a fresh database. `-json` gives machine-readable output.

## Signal-quality honesty

Three domains are known low/medium-signal and must degrade gracefully:

- `token-economics` — token/cost fields are usually absent from logs.
- `routing-accuracy` — no intent ground-truth exists; correlational at best.
- `knowledge-gaps` — medium-signal inference.

Record the caveat in the pack and emit "insufficient signal" rather than
fabricate a confident finding. This pairs with the confidence axis in
`../../references/calibration.md` (`<don't know>` is never surfaced).
