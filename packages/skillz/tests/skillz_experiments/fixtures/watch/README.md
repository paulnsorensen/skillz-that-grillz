# Watch browser fixture

Copy `run.json` to a temporary output directory. Start `watch` for that directory.
Open only the private URL printed by the command. Do not capture its fragment in screenshots.

| Section | Synthetic states |
| --- | --- |
| Status | Complete phase, call cap, accumulated call seconds, wall-clock budget, comparison, and gate verdict. |
| Roles | Task and judge usage, reflection usage, simulator usage, preflight calls, and failed-role seconds without usage. |
| Leaderboard | Search and baseline arms, scored zero, unscored evaluation failure, and non-search candidate hash. |
| Cases | Mixed zero and pass, all-zero case, unmet criterion reason, and failed case. |
| Reflections | Accepted proposal, rejected proposal, over-budget flag, and long script-like text. |
| Outcomes | Exact-JSON task usage fallback, genuine zero, failure placeholder, criteria, and candidate hash fallback. |

The transcript is intentionally private. `/api/run` must never return `PRIVATE_TRANSCRIPT_NOT_IN_UI`.
Delete or rewrite `run.json` to check waiting, error, and live refresh states.
