# Pack: skill-usage

- target_param: `{SKILL}` (a skill name or agent type)
- harness: `harness='all'` by default (`skill_invocations` is claude-dominant — note that)
- owner: skillz

Measures invocation patterns for `{SKILL}`. Run in one fresh read-only context.
Schema: `engine/references/canonical-schema.md` in this `skillz` skill.

## 1. Total invocations and date range

```sql
SELECT
    count(*) AS total_invocations,
    min(timestamp)::DATE AS first_seen,
    max(timestamp)::DATE AS last_seen,
    count(DISTINCT sessionId) AS unique_sessions
FROM skill_invocations
WHERE skill_name = '{SKILL}';
```

## 2. Weekly trend (last 8 weeks)

```sql
SELECT
    date_trunc('week', timestamp::DATE) AS week,
    count(*) AS invocations
FROM skill_invocations
WHERE skill_name = '{SKILL}'
  AND timestamp::DATE >= CURRENT_DATE - INTERVAL '56' DAY
GROUP BY week
ORDER BY week;
```

## 3. Project distribution

```sql
SELECT
    regexp_extract(cwd, '.*/([^/]+)$', 1) AS project,
    count(*) AS uses
FROM skill_invocations
WHERE skill_name = '{SKILL}'
GROUP BY project
ORDER BY uses DESC
LIMIT 10;
```

## 4. Peer comparison across the full skill distribution

```sql
WITH counts AS (
    SELECT skill_name AS target, count(*) AS total,
           count(DISTINCT (harness, sessionId)) AS sessions
    FROM skill_invocations GROUP BY skill_name
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
FROM distribution WHERE target = '{SKILL}';
```

The distribution uses every tracked skill before it filters the target.
An absent target or an empty table returns zero rows; report rank and median as unavailable.

## 5. If the target is an agent type, rank the full agent distribution

```sql
WITH counts AS (
    SELECT agent_type AS target, count(*) AS total,
           count(DISTINCT (harness, sessionId)) AS sessions
    FROM agent_spawns GROUP BY agent_type
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
FROM distribution WHERE target = '{SKILL}';
```

## Output Format

```
## Usage Analytics: {SKILL}

### Invocation Summary
- Total invocations: N (across N sessions)
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
