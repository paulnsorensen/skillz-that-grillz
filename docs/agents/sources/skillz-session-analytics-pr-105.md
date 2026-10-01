# feat(skillz): bundle session analytics engine

PR #105 is the session analytics integration proposed for skillz optimization.
Paul Sorensen's GitHub pull request, last verified 2026-09-28, bundles the analytics engine inside skillz.
It supplies observational evidence, not a GEPA evaluator.
Canonical source: [feat(skillz): bundle session analytics engine](https://github.com/paulnsorensen/skillz-that-grillz/pull/105).

## Snapshot and existing behavior

The session-analytics PR is open at commit `83a6a9050942a9e9c47f8cab84bad1f141203c33` in the inspected snapshot.
Its base is `main`; its branch is `paulnsorensen/session-analytics-engine`.[^1]
The checked-out skillz still uses an optional sibling session-analytics installation.[^2]
Do not describe the PR engine as shipped behavior.

The PR bundles ingestion scripts, canonical schema, coverage notes, and a smoke test.
It preserves the existing cache location and one-hour freshness window.
Without DuckDB, users can skip Usage or approve a bounded raw-log scan.
The PR reports that CI skips the DuckDB smoke test when DuckDB is absent.[^1]

## Evaluation-data limits

The PR schema explains why session logs are not directly replayable evaluation cases.[^3]

| Signal | Available evidence | Limitation for optimization |
| --- | --- | --- |
| `skill_invocations` | Claude `Skill` calls | Absence on another harness does not prove a missed trigger. |
| `tool_uses` | Arguments, tool names, session, timestamp | Observed commands do not establish intended outcomes. |
| `tool_results` | Error flags and result text | Materialized result text truncates at 500 characters. |
| `model_turns` | Model and usage fields for supported sources | Coverage differs; historical token totals can be incomplete. |
| `raw_entries` | Canonical post-adapter rows | Rows can differ from native transcripts. |

The coverage document excludes Cursor from error-rate comparisons because its transcripts lack tool-result blocks.
The Codex adapter omits native user, model, timing, and `event_msg` records.
Thus a user prompt may require approved access to the native transcript.
PR #105 can backfill Claude error flags, so error rates are lower bounds.[^4]

## Proposed use in skillz

A future dataset builder could use this engine to select candidate sessions and failure patterns.
It must then recover permitted task context, label an independent outcome, and verify replayability.
Join calls and results with `harness`, `sessionId`, and `tool_use_id`. Tool IDs can repeat across sessions.[^3][^4]
Keep unknown outcomes distinct from failures.
Report adapter coverage with every dataset.

See [the research synthesis](../research/gepa-session-optimization.md) and [the feature proposal](../ideas/session-driven-skill-optimization.md).
These links describe proposed behavior, not additions in PR #105.

[^1]: [PR #105](https://github.com/paulnsorensen/skillz-that-grillz/pull/105), body and metadata inspected 2026-09-28.
[^2]: `skills/skillz/references/analytics-ceremony.md:3-9` at `b068dacbae5b8afd8c3abe2f531adc5193a99e25`.
[^3]: [Pinned canonical schema](https://github.com/paulnsorensen/skillz-that-grillz/blob/83a6a9050942a9e9c47f8cab84bad1f141203c33/skills/skillz/engine/references/canonical-schema.md), read through GitHub Contents API, 2026-09-28.
[^4]: [Pinned harness coverage](https://github.com/paulnsorensen/skillz-that-grillz/blob/83a6a9050942a9e9c47f8cab84bad1f141203c33/skills/skillz/engine/references/harness-coverage.md), read through GitHub Contents API, 2026-09-28.

_Source: PR #105 and its pinned schema and coverage documents · Updated: 2026-09-28_
