# Pack: agent-orchestration

- target_param: `{TARGET}` (the skill or agent type whose orchestration we audit)
- target_kind: `skill` or `agent`, from the dispatch prompt
- start table: set `{START_TABLE}` and `{NAME_COLUMN}` from `target_kind`
  - `skill`: `skill_invocations`, `skill_name`
  - `agent`: `agent_spawns`, `agent_type`
- harness: `harness='all'` (`agent_spawns` / `mcp_calls` are claude-dominant — note that)
- owner: skillz

Report tools, agents, and MCP calls inside each 10-minute post-invocation window.
These events are temporally correlated. The window does not prove causation or concurrency.
Run in one fresh read-only context. Schema: `engine/references/canonical-schema.md`
in this `skillz` skill. Replace each placeholder before you run a query.

For an `agent` target, a window starts at each spawn of that agent in the parent session.
The spawned agent's own tool calls carry no join key. The tool, MCP, and spawn tables
count parent-session events in the window, so state that limit in the findings.

First run `SELECT harness, count(*) AS n FROM {START_TABLE} GROUP BY harness;`.
Report a harness absent from the result as `unavailable`, never as 0.

## 1. Tools correlated with invocation windows

```sql
WITH windows AS (
    SELECT harness, sessionId, timestamp::TIMESTAMP AS t0,
           timestamp::TIMESTAMP + INTERVAL '10' MINUTE AS t1
    FROM {START_TABLE} WHERE {NAME_COLUMN} = '{TARGET}'
)
SELECT tu.tool_name, count(*) AS uses
FROM tool_uses tu
WHERE EXISTS (
    SELECT 1 FROM windows w
    WHERE w.harness = tu.harness AND w.sessionId = tu.sessionId
      AND tu.timestamp::TIMESTAMP BETWEEN w.t0 AND w.t1
)
GROUP BY tu.tool_name ORDER BY uses DESC;
```

`EXISTS` counts each tool event once when invocation windows overlap.

## 2. Agent types correlated with invocation windows

```sql
WITH windows AS (
    SELECT harness, sessionId, timestamp::TIMESTAMP AS t0,
           timestamp::TIMESTAMP + INTERVAL '10' MINUTE AS t1
    FROM {START_TABLE} WHERE {NAME_COLUMN} = '{TARGET}'
)
SELECT asp.agent_type, substr(asp.description, 1, 80) AS agent_description,
       asp.mode, count(*) AS spawns
FROM agent_spawns asp
WHERE EXISTS (
    SELECT 1 FROM windows w
    WHERE w.harness = asp.harness AND w.sessionId = asp.sessionId
      AND asp.timestamp::TIMESTAMP BETWEEN w.t0 AND w.t1
)
GROUP BY asp.agent_type, asp.description, asp.mode
ORDER BY spawns DESC;
```

## 3. MCP calls correlated with invocation windows

```sql
WITH windows AS (
    SELECT harness, sessionId, timestamp::TIMESTAMP AS t0,
           timestamp::TIMESTAMP + INTERVAL '10' MINUTE AS t1
    FROM {START_TABLE} WHERE {NAME_COLUMN} = '{TARGET}'
)
SELECT mc.harness, mc.tool_name, count(*) AS calls
FROM mcp_calls mc
WHERE EXISTS (
    SELECT 1 FROM windows w
    WHERE w.harness = mc.harness AND w.sessionId = mc.sessionId
      AND mc.timestamp::TIMESTAMP BETWEEN w.t0 AND w.t1
)
GROUP BY mc.harness, mc.tool_name ORDER BY calls DESC;
```

Keep the full MCP tool name because Pi-family and Claude-family names use different separators.

## 4. Largest correlated spawn windows

```sql
WITH windows AS (
    SELECT harness, sessionId, timestamp::TIMESTAMP AS window_start,
           timestamp::TIMESTAMP + INTERVAL '10' MINUTE AS window_end
    FROM {START_TABLE} WHERE {NAME_COLUMN} = '{TARGET}'
)
SELECT w.harness, w.sessionId, w.window_start,
       count(asp.sessionId) AS correlated_spawns
FROM windows w
LEFT JOIN agent_spawns asp
    ON asp.harness = w.harness AND asp.sessionId = w.sessionId
   AND asp.timestamp::TIMESTAMP BETWEEN w.window_start AND w.window_end
GROUP BY w.harness, w.sessionId, w.window_start
ORDER BY correlated_spawns DESC, w.window_start DESC LIMIT 10;
```

This ranking includes zero-spawn windows. It does not measure parallel fan-out.
One spawn can appear in multiple overlapping windows, so do not sum this table.

## Output Format

```
## Orchestration Analytics: {TARGET}

### Tool Usage (correlated windows)
| Tool | Uses |
|------|------|

### Agent Spawns (correlated windows)
| Agent Type | Description | Mode | Count |
|------------|-------------|------|-------|

### MCP Usage (correlated windows)
| Harness | MCP Tool | Calls |
|---------|----------|-------|

### Largest Correlated Spawn Windows
| Harness | Session | Window Start | Correlated Spawns |
|---------|---------|--------------|-------------------|

### Harnesses without {START_TABLE} events
- [List as `unavailable`, or none]

### Findings
- [For an agent target, state that the windows are session-window correlation]
- [Declared-vs-observed tool, agent, or MCP mismatch]
- [State that temporal correlation does not establish causation or concurrency]
```
