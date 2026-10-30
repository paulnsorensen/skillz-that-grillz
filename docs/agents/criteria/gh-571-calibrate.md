# gh-571-calibrate: the plan estimate charges the measured per-call time

Source: https://github.com/paulnsorensen/skillz-that-grillz/issues/571

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN `run` sizes a run THE SYSTEM SHALL charge `ESTIMATE_SECONDS_PER_CALL` (30) seconds per call, over the concurrency, in `estimate.seconds` and in the fit against the seconds cap, and record `estimate.seconds_per_call`. |  |
| AC-2 | test | WHEN a run is approved THE SYSTEM SHALL reserve 60 seconds per gate call, over the concurrency, capped by the run's seconds cap, size the plan so that the search time fits after that reserve, and give each call a timeout base of 4 times (60 plus the sandbox seconds). |  |
| AC-3 | test | WHEN the shipped skillz journey run is sized THE SYSTEM SHALL add no `reflection_time_warning`. |  |
| AC-4 | test | WHEN a journey plan stop names a cap THE SYSTEM SHALL not say that the seconds cap binds first, because the call cap binds first at the plan rate. |  |
| AC-5 | test | WHEN a run whose `run.json` estimate has no `seconds_per_call` resumes THE SYSTEM SHALL stop with the older-runner message, not `run-record-tampered`. |  |
| AC-6 | process | `skills/skillz/references/experiments.md`, ADR-005, and ADR-010 state the two rates. | review of the pull request |
| AC-7 | process | `just build` exits 0. | `just build` exits 0 |
