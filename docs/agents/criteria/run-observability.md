# run-observability: query and watch an autoimprove run from run.json

Source: reconstructed from the run-observability task brief (2026-11-07), with evidence from the run.json probe of PR #689

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN an agent runs the `case-scores`, `unmet-criteria`, and `dealbreakers` queries of `references/run-analytics.md` over a `run.json` THE SYSTEM SHALL list each case with its zero-score runs, each criterion that the judge did not accept with its lowest level and the judge reason of that level, and each `must` criterion at level 1 or 2 with its text from `cases.json` |  |
| AC-2 | test | WHEN an agent runs the `score-by-arm` query THE SYSTEM SHALL give one row per split, arm, and candidate with its cases, runs, scored runs, mean score, and zero-score runs, best mean first in each split |  |
| AC-3 | test | WHEN an agent runs the `run-status` and `role-usage` queries over a glob of `run.json` files THE SYSTEM SHALL give one row per run with its phase, calls and seconds against their caps, search stop, and gate verdict, and one row per run and role with its calls, seconds, tokens, and latency |  |
| AC-4 | test | WHEN an agent runs the `reflections` query THE SYSTEM SHALL give each proposer step in run order with its hypothesis, notes, parent, proposed candidate, and changed-file count |  |
| AC-5 | test | WHEN the pack changes THE SYSTEM SHALL keep one `run-analytics.md` section per query, each with a `{RUN_JSON}` placeholder and no statement that writes |  |
