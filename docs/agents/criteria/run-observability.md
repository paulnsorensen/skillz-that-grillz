# run-observability: query and watch an autoimprove run from run.json

Source: reconstructed from the run-observability task brief (2026-11-07), with evidence from the run.json probe of PR #689

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN an agent runs the `case-scores`, `unmet-criteria`, and `dealbreakers` queries of `references/run-analytics.md` over a `run.json` THE SYSTEM SHALL list each case with its judged zero-score runs (not evaluation-failed placeholders), each criterion that the judge did not accept with its lowest level and the judge reason of that level, and each `must` criterion at level 1 or 2 with its text from `cases.json` |  |
| AC-2 | test | WHEN an agent runs the `score-by-arm` query THE SYSTEM SHALL give one row per split, arm, and candidate with its cases, runs, judged scored runs, mean score, and genuine zero-score runs, best mean first in each split |  |
| AC-3 | test | WHEN an agent runs the `run-status` and `role-usage` queries over a glob of `run.json` files THE SYSTEM SHALL give one row per run with its phase, calls against the call cap, accumulated call seconds separate from the wall-clock budget, search stop, and gate verdict, and one row per recorded role with its calls, seconds, available tokens, and latency. Task usage falls back to `usage` only when `task_usage` is absent |  |
| AC-4 | test | WHEN an agent runs the `reflections` query THE SYSTEM SHALL give each proposer step in run order with its hypothesis, notes, parent, proposed candidate, and changed-file count |  |
| AC-5 | test | WHEN the pack changes THE SYSTEM SHALL keep one `run-analytics.md` section per query, each with a `{RUN_JSON}` placeholder and no statement that writes |  |
| AC-6 | test | WHEN a user runs `skillz-experiment watch <out>` from the source package or the bundled archive THE SYSTEM SHALL listen on 127.0.0.1 only, print a private capability URL as the first stdout line, exit 0 on an interrupt, and stop with a coded error for a port outside 0 to 65535 or a port in use |  |
| AC-7 | test | WHEN the runner writes or rewrites `<out>/run.json` while `watch` runs THE SYSTEM SHALL serve, without a restart, the phase, calls and accumulated call seconds separate from the wall-clock budget, each role, the leaderboard, each case with its criteria and judge reasons, the reflections, and the newest outcomes |  |
| AC-8 | test | WHEN a client calls the `watch` server THE SYSTEM SHALL answer only GET requests for the page, its script, its style, and `/api/run`, only for a Host header of its own loopback address, and require the printed private capability for run data, with a same-origin content security policy, without a transcript or candidate file in the data, and without a write under `<out>` |  |
| AC-9 | test | WHEN `run.json` is absent, is not JSON, is a symlink, or holds malformed fields THE SYSTEM SHALL report the waiting or error state, or leave out the malformed parts, and SHALL show the record again once it is valid |  |

## Accounting and private access

Run observability counts evaluation failures as attempts, not judged scores. A failure placeholder does not lower a candidate mean.
Exact-JSON task usage comes from `usage` only when `task_usage` is absent. Recorded calls and seconds keep a role visible without usage.[^1]

The watcher protects private run data with a random capability in its printed URL. Loopback and Host checks remain separate restrictions.
Keep the printed URL private. The browser sends the capability with data requests, not with public static assets.[^2]

Accumulated call seconds can overlap during concurrent calls. They do not measure elapsed wall-clock budget use.[^3]

[^1]: packages/skillz/src/skillz_experiments/run/_watch_view.py; skills/skillz/references/run-analytics.md
[^2]: packages/skillz/src/skillz_experiments/run/_watch.py; packages/skillz/src/skillz_experiments/run/_watch_page.py
[^3]: packages/skillz/src/skillz_experiments/_budget.py; packages/skillz/src/skillz_experiments/run/_holdout.py

_Source: PR #691 review and approved capability access contract · Updated: 2026-11-08_
