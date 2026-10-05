# Pack: skill-usage

- target_param: `{TARGET}` (a skill name or agent type)
- target_kind: `skill` or `agent`, from the dispatch prompt
- start table: set `{START_TABLE}` and `{NAME_COLUMN}` from `target_kind`
  - `skill`: `skill_invocations`, `skill_name`
  - `agent`: `agent_spawns`, `agent_type`
- harness: `harness='all'` by default (`skill_invocations` and `agent_spawns` are claude-dominant — note that)
- owner: skillz

Measures invocation patterns for `{TARGET}`. Run in one fresh read-only context.
Schema: `engine/references/canonical-schema.md` in this `skillz` skill.
Replace each placeholder before you run a query.

## 0. Source coverage

```sql
SELECT harness, count(*) AS n FROM {START_TABLE} GROUP BY harness;
```

A harness absent from this result has no `{START_TABLE}` events.
Report that harness as `unavailable`, never as 0.
If the query returns no rows, report all usage as `unavailable`.
## 1. Total invocations and date range

```sql
SELECT
    count(*) AS total_invocations,
    min(timestamp)::DATE AS first_seen,
    max(timestamp)::DATE AS last_seen,
    count(DISTINCT sessionId) AS unique_sessions
FROM {START_TABLE}
WHERE {NAME_COLUMN} = '{TARGET}';
```

## 2. Weekly trend (last 8 weeks)

```sql
SELECT
    date_trunc('week', timestamp::DATE) AS week,
    count(*) AS invocations
FROM {START_TABLE}
WHERE {NAME_COLUMN} = '{TARGET}'
  AND timestamp::DATE >= CURRENT_DATE - INTERVAL '56' DAY
GROUP BY week
ORDER BY week;
```

## 3. Project distribution

```sql
SELECT
    regexp_extract(cwd, '.*/([^/]+)$', 1) AS project,
    count(*) AS uses
FROM {START_TABLE}
WHERE {NAME_COLUMN} = '{TARGET}'
GROUP BY project
ORDER BY uses DESC
LIMIT 10;
```

## 4. Peer comparison across the full distribution

```sql
WITH counts AS (
    SELECT {NAME_COLUMN} AS target, count(*) AS total,
           count(DISTINCT (harness, sessionId)) AS sessions
    FROM {START_TABLE} GROUP BY {NAME_COLUMN}
), distribution AS (
    SELECT target, total, sessions,
           rank() OVER (ORDER BY total DESC) AS target_rank,
           count(*) OVER () AS population,
           median(total) OVER () AS median_total
    FROM counts
)
SELECT target, total, sessions, target_rank, population, median_total,
       CASE WHEN total > median_total THEN 'above'
            WHEN total < median_total THEN 'below' ELSE 'at' END AS versus_median
FROM distribution WHERE target = '{TARGET}';
```

The distribution uses every tracked skill or agent type before it filters the target.
An absent target or an empty table returns zero rows; report rank and median as unavailable.

## Output Format

```
## Usage Analytics: {TARGET}

### Invocation Summary
- Total invocations: N (across N sessions), or `unavailable` for a harness without events
- Harnesses without events: [list, or none]
- Active since: YYYY-MM-DD
- Last used: YYYY-MM-DD

### Trend
- Direction: rising / stable / declining / new (<4 weeks)
- Weekly average (last 4 weeks): N
- Weekly average (prior 4 weeks): N

### Project Distribution
| Project | Uses |
|---------|------|

### Peer Ranking
- Target total: N
- Rank N of M tracked skills or agent types
- Population median: N
- Usage relative to median: above / at / below
- If the target is absent or the table is empty: rank and median unavailable

### Findings
- [Notable patterns: zero usage, sharp decline, single-project concentration]
```
