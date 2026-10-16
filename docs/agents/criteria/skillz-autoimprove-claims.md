# skillz-autoimprove-claims: validate the three autoimprove claims separately

Source: spec skillz-autoimprove-claims (approved 2026-10-15)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN export and replay build a patch THE SYSTEM SHALL use one shared builder, and export SHALL refuse a patch whose digest differs from the replayed digest. |  |
| AC-2 | test | WHEN a run reaches the replay phase with a winner that differs from the seed THE SYSTEM SHALL apply the exported patches with `git apply` to a clean copy outside the target, and SHALL check byte equality with the evaluated winner. |  |
| AC-3 | test | WHEN the patch fails to apply, the bytes differ, or the rebuilt identity differs THE SYSTEM SHALL record the export claim as `refuted` with the matching reason. |  |
| AC-4 | test | WHEN the replay runs THE SYSTEM SHALL evaluate the candidate built from the patched copy once on every holdout case through the harness, and SHALL record per-case score differences against the winner holdout scores. |  |
| AC-5 | test | WHEN the replay budget is short or the replay scores drift THE SYSTEM SHALL record the export claim as `inconclusive` and SHALL make no call beyond the approved budget. |  |
| AC-6 | test | WHEN a run resumes during the replay phase THE SYSTEM SHALL not repeat a scored replay outcome. |  |
| AC-7 | test | WHEN the plan sizes a run THE SYSTEM SHALL include `replay_calls` in the estimate, the approved calls, and the holdout reserve. |  |
| AC-8 | test | WHEN a run completes THE SYSTEM SHALL report `claims.runner`, `claims.export`, and `claims.improvement` separately, each with one status from `supported`, `refuted`, and `inconclusive`. |  |
| AC-9 | test | WHEN the gate verdict is `inconclusive` or `promote-cheaper` THE SYSTEM SHALL report the improvement claim as `inconclusive` and `claims.apply` as false. |  |
| AC-10 | test | WHEN any runner check fails THE SYSTEM SHALL report the runner claim as `refuted`. |  |
| AC-11 | test | WHEN the improvement claim is reported THE SYSTEM SHALL include per-family case counts and mean paired deltas. |  |
| AC-12 | test | WHEN the judge scores a holdout outcome THE SYSTEM SHALL send no arm label and no candidate identity in the judge prompt. |  |
| AC-13 | test | WHEN the experiments reference describes reporting THE SYSTEM SHALL require a representative example before the case draft and the three claims in the report. |  |
| AC-14 | test | WHEN a replay call times out or hits another provider fault THE SYSTEM SHALL record an unscored `evaluation-failed` replay outcome with its reason, and SHALL leave the export claim `inconclusive`, never `refuted`. |  |
| AC-15 | test | WHEN replay calls run, including a resume past the deadline in the `gated` or `replayed` phase, THE SYSTEM SHALL keep `calls` equal to the sum of `calls_by_role`. |  |
| AC-16 | test | WHEN a run completes THE SYSTEM SHALL report `claims` in the summary and in `report.json` next to `retries`, `calls_by_role`, `seconds_by_role`, and the search `scored` and `failed` counts. |  |
