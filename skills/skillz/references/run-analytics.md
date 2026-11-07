# Run analytics

Use the `watch` page or these DuckDB queries to see how an `autoimprove` run goes.
Both read `<out>/run.json`, which the runner rewrites after each case evaluation.
You can use them while the run works, or after it completes.

## Watch a run

When the user wants to follow a run, start the page in the background:

```bash
python3 scripts/skillz-experiment.pyz watch <out>
```

The first stdout line is JSON with the page `url`. Give that URL to the user.
The page shows the phase, the calls and seconds against their caps, each role, and the leaderboard.
It also shows each case with the judge reason of each unmet criterion, the reflections, and the newest outcomes.
It reloads the data every 2 seconds. Stop it with Ctrl-C when the user is done.

The page listens only on 127.0.0.1, answers only GET requests, and writes no file.
It shows no transcript and no candidate file. `--port N` sets the port; the default 0 picks a free port.
The page does not need DuckDB.

## Inputs

- `{RUN_JSON}`: the path of one `run.json`, or a glob such as `/var/tmp/out.*/run.json`. A glob compares runs.
- `{CASES_JSON}`: the path of `<out>/cases.json`. Only the `dealbreakers` query reads it.

Replace each placeholder with a literal path before you run a query.

## Running queries

Run each query with the DuckDB CLI and no database file:

```bash
duckdb -init /dev/null -json -c "SQL"
```

The queries only read files. They create no table and write no file.
The runner does not need DuckDB.

`run.json` holds private inputs: case text, transcripts, judge reasons, and candidate files.
The queries select no transcript and no candidate file.
Keep the results local. Do not paste a judge reason into a public issue without the user's approval.

## Reading the results

- A case with `zero_runs` equal to `scored` in `case-scores` never scores above 0.
  Read `dealbreakers` first: a `must` criterion at level 1 or 2 scores the case 0.
- `unmet-criteria` gives each criterion that the judge did not accept, its lowest level, and the judge reason of that level.
- `score-by-arm` compares the seed and each candidate on the same split. Compare means only on the same split.
- `role-usage` shows where the calls, tokens, and seconds go. `run-status` compares the calls and seconds with their caps.
- `reflections` gives each proposer hypothesis in order. `run-status` gives the search `stop_trigger` and the gate verdict.
- An empty result means no data for that query. Report it as no data, not as 0.

## Queries

### `run-status`

One row per run: phase, calls and seconds against their caps, search stop, and gate verdict.

```sql
WITH runs AS (
    SELECT filename AS run_file, content::JSON AS run
    FROM read_text('{RUN_JSON}')
)
SELECT
    run_file,
    run ->> 'run_id' AS run_id,
    run ->> 'phase' AS phase,
    (run ->> 'calls')::INTEGER AS calls,
    (run -> 'budget' ->> 'maximum')::INTEGER AS call_cap,
    round(100.0 * calls / nullif(call_cap, 0), 1) AS calls_pct,
    round(list_sum(CAST(json_extract(run, '$.seconds_by_role.*') AS DOUBLE[])), 1) AS seconds,
    (run -> 'budget' ->> 'seconds')::DOUBLE AS seconds_cap,
    run -> 'search' ->> 'stop_trigger' AS stop_trigger,
    run -> 'search' ->> 'reason' AS search_reason,
    run -> 'gate' ->> 'verdict' AS gate_verdict,
    (run -> 'gate' ->> 'delta')::DOUBLE AS gate_delta,
    len(list_filter(json_extract_string(run, '$.outcomes[*].split'), lambda s: s <> 'reflection')) AS outcomes,
    len(list_filter(json_extract_string(run, '$.outcomes[*].split'), lambda s: s = 'reflection')) AS reflections
FROM runs
ORDER BY run_file;
```

### `role-usage`

One row per run and role: recorded calls and seconds, then the tokens and latency of the outcomes of that role.

```sql
WITH runs AS (
    SELECT filename AS run_file, content::JSON AS run
    FROM read_text('{RUN_JSON}')
),
outcomes AS (
    SELECT run_file, unnest(json_extract(run, '$.outcomes[*]')) AS o
    FROM runs
),
usage AS (
    SELECT run_file, 'task' AS role, o -> 'task_usage' AS u, o ->> 'latency_seconds' AS latency
    FROM outcomes WHERE (o ->> 'split') <> 'reflection'
    UNION ALL
    SELECT run_file, 'judge', o -> 'judge_usage', o ->> 'judge_latency_seconds'
    FROM outcomes WHERE (o ->> 'split') <> 'reflection'
    UNION ALL
    SELECT run_file, 'simulator', o -> 'simulator_usage', NULL
    FROM outcomes WHERE (o ->> 'split') <> 'reflection'
    UNION ALL
    SELECT run_file, 'reflection', o -> 'usage', o ->> 'latency_seconds'
    FROM outcomes WHERE (o ->> 'split') = 'reflection'
)
SELECT
    usage.run_file,
    role,
    any_value((run -> 'calls_by_role' ->> role)::INTEGER) AS calls,
    any_value(round((run -> 'seconds_by_role' ->> role)::DOUBLE, 1)) AS seconds,
    count(*) AS usage_rows,
    sum((u ->> 'input_tokens')::BIGINT)::BIGINT AS input_tokens,
    sum((u ->> 'cached_input_tokens')::BIGINT)::BIGINT AS cached_input_tokens,
    sum((u ->> 'output_tokens')::BIGINT)::BIGINT AS output_tokens,
    round(avg(latency::DOUBLE), 1) AS mean_latency_s,
    round(max(latency::DOUBLE), 1) AS max_latency_s
FROM usage JOIN runs USING (run_file)
WHERE json_type(u) = 'OBJECT'
GROUP BY usage.run_file, role
ORDER BY usage.run_file, calls DESC NULLS LAST, role;
```

### `score-by-arm`

One row per run, split, arm, and candidate. `candidate` is the first 12 characters of the candidate identity.

```sql
WITH runs AS (
    SELECT filename AS run_file, content::JSON AS run
    FROM read_text('{RUN_JSON}')
),
outcomes AS (
    SELECT run_file, unnest(json_extract(run, '$.outcomes[*]')) AS o
    FROM runs
),
scored AS (
    SELECT
        run_file,
        o ->> 'split' AS split,
        o ->> 'arm' AS arm,
        left(coalesce(o ->> 'search_candidate', o ->> 'candidate_hash'), 12) AS candidate,
        o ->> 'case_id' AS case_id,
        (o ->> 'score')::DOUBLE AS score
    FROM outcomes WHERE (o ->> 'split') <> 'reflection'
)
SELECT
    run_file, split, arm, candidate,
    count(DISTINCT case_id) AS cases,
    count(*) AS runs,
    count(score) AS scored,
    round(avg(score), 3) AS mean_score,
    count(*) FILTER (WHERE score = 0) AS zero_runs
FROM scored
GROUP BY run_file, split, arm, candidate
ORDER BY run_file, split, mean_score DESC NULLS LAST, candidate;
```

### `case-scores`

One row per run, case, and split. The cases that score 0 most often come first. `statuses` lists the fault statuses.

```sql
WITH runs AS (
    SELECT filename AS run_file, content::JSON AS run
    FROM read_text('{RUN_JSON}')
),
outcomes AS (
    SELECT run_file, unnest(json_extract(run, '$.outcomes[*]')) AS o
    FROM runs
),
scored AS (
    SELECT
        run_file,
        o ->> 'case_id' AS case_id,
        o ->> 'split' AS split,
        (o ->> 'score')::DOUBLE AS score,
        o ->> 'status' AS status
    FROM outcomes WHERE (o ->> 'split') <> 'reflection'
)
SELECT
    run_file, case_id, split,
    count(*) AS runs,
    count(score) AS scored,
    count(*) FILTER (WHERE score = 0) AS zero_runs,
    round(avg(score), 3) AS mean_score,
    round(min(score), 3) AS min_score,
    round(max(score), 3) AS max_score,
    list(DISTINCT status ORDER BY status) FILTER (WHERE status IS NOT NULL) AS statuses
FROM scored
GROUP BY run_file, case_id, split
ORDER BY run_file, zero_runs DESC, mean_score NULLS FIRST, case_id, split;
```

### `unmet-criteria`

One row per run, case, and criterion that the judge did not accept at least once.
`reason` is the judge reason of the lowest unmet level.

```sql
WITH runs AS (
    SELECT filename AS run_file, content::JSON AS run
    FROM read_text('{RUN_JSON}')
),
outcomes AS (
    SELECT run_file, unnest(json_extract(run, '$.outcomes[*]')) AS o
    FROM runs
),
judged AS (
    SELECT run_file, o ->> 'case_id' AS case_id, o -> 'criteria' AS criteria,
           unnest(json_keys(o -> 'criteria')) AS criterion
    FROM outcomes WHERE json_type(o -> 'criteria') = 'OBJECT'
),
verdicts AS (
    SELECT
        run_file, case_id, criterion,
        (criteria -> criterion ->> 'level')::INTEGER AS level,
        (criteria -> criterion ->> 'met')::BOOLEAN AS met,
        criteria -> criterion ->> 'reason' AS reason
    FROM judged WHERE json_type(criteria -> criterion) = 'OBJECT'
)
SELECT
    run_file, case_id, criterion,
    count(*) AS runs,
    count(*) FILTER (WHERE NOT met) AS unmet_runs,
    min(level) FILTER (WHERE NOT met) AS lowest_level,
    arg_min(reason, level) FILTER (WHERE NOT met) AS reason
FROM verdicts
GROUP BY run_file, case_id, criterion
HAVING count(*) FILTER (WHERE NOT met) > 0
ORDER BY run_file, case_id, unmet_runs DESC, criterion;
```

### `dealbreakers`

One row per run, case, and `must` criterion that the judge put at level 1 or 2.
Each such run scores the case 0. `text` is the criterion text from `cases.json`.

```sql
WITH runs AS (
    SELECT filename AS run_file, content::JSON AS run
    FROM read_text('{RUN_JSON}')
),
outcomes AS (
    SELECT run_file, unnest(json_extract(run, '$.outcomes[*]')) AS o
    FROM runs
),
judged AS (
    SELECT run_file, o ->> 'case_id' AS case_id, o -> 'criteria' AS criteria,
           unnest(json_keys(o -> 'criteria')) AS criterion
    FROM outcomes WHERE json_type(o -> 'criteria') = 'OBJECT'
),
verdicts AS (
    SELECT
        run_file, case_id, criterion,
        (criteria -> criterion ->> 'level')::INTEGER AS level,
        criteria -> criterion ->> 'reason' AS reason
    FROM judged WHERE json_type(criteria -> criterion) = 'OBJECT'
),
cases AS (
    SELECT unnest(json_extract(content::JSON, '$.cases[*]')) AS c
    FROM read_text('{CASES_JSON}')
),
musts AS (
    SELECT c ->> 'id' AS case_id, unnest(json_extract(c, '$.expected.criteria[*]')) AS definition
    FROM cases
)
SELECT
    v.run_file, v.case_id, v.criterion,
    count(*) AS zeroed_runs,
    min(v.level) AS lowest_level,
    any_value(m.definition ->> 'text') AS text,
    arg_min(v.reason, v.level) AS reason
FROM verdicts AS v
JOIN musts AS m ON m.case_id = v.case_id AND (m.definition ->> 'id') = v.criterion
WHERE (m.definition ->> 'must')::BOOLEAN AND v.level <= 2
GROUP BY v.run_file, v.case_id, v.criterion
ORDER BY v.run_file, v.case_id, v.criterion;
```

### `reflections`

One row per proposer step, in run order. `parent` and `proposed` are the first 12 characters of each candidate identity.

```sql
WITH runs AS (
    SELECT filename AS run_file, content::JSON AS run
    FROM read_text('{RUN_JSON}')
),
steps AS (
    SELECT
        run_file,
        unnest(json_extract(run, '$.outcomes[*]')) AS o,
        generate_subscripts(json_extract(run, '$.outcomes[*]'), 1) AS position
    FROM runs
)
SELECT
    run_file,
    row_number() OVER (PARTITION BY run_file ORDER BY position) AS step,
    o ->> 'hypothesis' AS hypothesis,
    o ->> 'notes' AS notes,
    left(o ->> 'parent', 12) AS parent,
    left(o ->> 'proposed', 12) AS proposed,
    json_array_length(o -> 'changed')::INTEGER AS changed,
    (o ->> 'over_budget')::BOOLEAN AS over_budget,
    o ->> 'rejected' AS rejected,
    round((o ->> 'latency_seconds')::DOUBLE, 1) AS latency_s,
    (o -> 'usage' ->> 'output_tokens')::BIGINT AS output_tokens
FROM steps
WHERE (o ->> 'split') = 'reflection'
ORDER BY run_file, step;
```
