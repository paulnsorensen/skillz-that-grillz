# Pack: drift-regression

- target_param: `{TARGET}` (a skill name or agent type)
- target_kind: `skill` or `agent`, from the dispatch prompt
- start table: set `{START_TABLE}` and `{NAME_COLUMN}` from `target_kind`, as the
  Substitution section of `engine/references/query-conventions.md` defines
- harness: `harness='all'` (`skill_invocations` and `agent_spawns` are claude-dominant — note that)
- owner: skillz

Detect usage decay and error-rate changes for `{TARGET}`.
Tool events inside invocation windows are temporal correlations, not attributed effects.
Run in one fresh read-only context. Schema: `engine/references/canonical-schema.md`
in this `skillz` skill. Replace each placeholder before you run a query.

For an `agent` target, a window starts at each spawn of that agent in the session.
`agent_spawns` has no join key to the spawned agent's own tool calls.
Claude sidechain events share the parent `sessionId`, so a window includes them,
mixed across concurrent spawns. The tables cannot attribute them to one spawn.
Report the error trend as session-window correlation and state that limit.
Each window includes events at its start time but excludes its own anchor event
(`tool_use_id IS DISTINCT FROM anchor_id`).
Cursor timestamps have minute resolution, so many events share the anchor timestamp.
State that limit when Cursor rows appear.

First run this coverage query.

```sql
SELECT harness, count(*) AS n FROM {START_TABLE} GROUP BY harness;
```

Report a harness absent from the result as `unavailable`, never as 0.
If the query returns no rows, report the verdict as `unavailable`, not `dormant`.

## 1. Usage decay (recent vs prior 4 weeks)

```sql
WITH inv AS (SELECT timestamp::DATE AS d FROM {START_TABLE} WHERE {NAME_COLUMN} = '{TARGET}')
SELECT
    sum(CASE WHEN d >= CURRENT_DATE - INTERVAL '28' DAY
              AND d < CURRENT_DATE THEN 1 ELSE 0 END) AS recent_4w,
    sum(CASE WHEN d >= CURRENT_DATE - INTERVAL '56' DAY
              AND d < CURRENT_DATE - INTERVAL '28' DAY THEN 1 ELSE 0 END) AS prior_4w
FROM inv;
```

Each window contains exactly 28 completed calendar dates.
Both windows exclude today and future dates.

## 2. Error-rate trend for correlated tool events

```sql
WITH windows AS (
    SELECT harness, sessionId, anchor_id, timestamp::TIMESTAMP AS t0,
           timestamp::TIMESTAMP + INTERVAL '10' MINUTE AS t1
    FROM {START_TABLE} WHERE {NAME_COLUMN} = '{TARGET}'
),
correlated_calls AS (
    SELECT tu.harness, tu.sessionId, tu.tool_use_id,
           tu.timestamp::DATE AS event_day
    FROM tool_uses tu
    WHERE EXISTS (
        SELECT 1 FROM windows w
        WHERE w.harness = tu.harness AND w.sessionId = tu.sessionId
          AND tu.timestamp::TIMESTAMP BETWEEN w.t0 AND w.t1
          AND tu.tool_use_id IS DISTINCT FROM w.anchor_id
    )
)
SELECT date_trunc('week', cc.event_day) AS week,
       count(*) AS calls,
       round(sum(CASE WHEN tr.is_error = 'true' THEN 1 ELSE 0 END)
             * 100.0 / count(*), 1) AS error_pct
FROM correlated_calls cc
JOIN tool_results tr
  ON tr.harness = cc.harness
 AND tr.sessionId = cc.sessionId
 AND tr.tool_use_id = cc.tool_use_id
GROUP BY week ORDER BY week;
```

`EXISTS` counts each tool event once across overlapping windows.
The week comes from the tool event, not the invocation.

## 3. New correlated error signatures in the last 2 weeks

```sql
WITH windows AS (
    SELECT harness, sessionId, anchor_id, timestamp::TIMESTAMP AS t0,
           timestamp::TIMESTAMP + INTERVAL '10' MINUTE AS t1
    FROM {START_TABLE} WHERE {NAME_COLUMN} = '{TARGET}'
),
correlated_calls AS (
    SELECT tu.harness, tu.sessionId, tu.tool_use_id,
           tu.timestamp::DATE AS event_day
    FROM tool_uses tu
    WHERE tu.timestamp::DATE < CURRENT_DATE
      AND EXISTS (
        SELECT 1 FROM windows w
        WHERE w.harness = tu.harness AND w.sessionId = tu.sessionId
          AND tu.timestamp::TIMESTAMP BETWEEN w.t0 AND w.t1
          AND tu.tool_use_id IS DISTINCT FROM w.anchor_id
      )
)
SELECT substr(tr.content, 1, 120) AS error, count(*) AS occurrences,
       min(cc.event_day) AS first_seen
FROM correlated_calls cc
JOIN tool_results tr
  ON tr.harness = cc.harness
 AND tr.sessionId = cc.sessionId
 AND tr.tool_use_id = cc.tool_use_id
WHERE tr.is_error = 'true'
GROUP BY tr.content
HAVING min(cc.event_day) >= CURRENT_DATE - INTERVAL '14' DAY
   AND min(cc.event_day) < CURRENT_DATE
ORDER BY occurrences DESC LIMIT 10;
```

The 14-day window contains completed calendar dates from 14 days ago through yesterday.
The historical `first_seen` test uses all earlier completed tool-event dates.
Grouping uses the full error content; truncation affects display only.

## Output Format

```
## Drift / Regression Analytics: {TARGET}

### Usage Trajectory
- Recent 4 weeks: N invocations
- Prior 4 weeks: N invocations
- Verdict: growing / stable / decaying / dormant / unavailable
- Harnesses without {START_TABLE} events: [list as `unavailable`, or none]

### Correlated Error-Rate Trend
| Tool Event Week | Calls | Error % |
|-----------------|-------|---------|
- Direction: improving / stable / regressing

### New Correlated Error Signatures (last 2 weeks)
| Error | Count | First seen from tool event |
|-------|-------|----------------------------|

### Findings
- [Usage decay, correlated error-rate regression, or newly observed failures]
- [State that temporal correlation does not establish causation]
- "Insufficient signal" if <4 weeks of data or all-empty.
```
