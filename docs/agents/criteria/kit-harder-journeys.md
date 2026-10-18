# kit-harder-journeys: harder journeys for the self-autoimprove kit

Source: kit-harder-journeys spec (approved 2026-10-17; absent from the repository)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN `journeys.py write` runs THE SYSTEM SHALL write 18 journey cases in 6 families: `ask-harness`, `revise-cases`, `failed-run`, `unsafe-out`, `stale-hash`, `just-run`, and no `named-harness` case |  |
| AC-2 | test | WHEN the cases split at seed 20261006 THE SYSTEM SHALL put `revise-cases` and `unsafe-out` in train, `ask-harness` and `stale-hash` in validation, and `failed-run` and `just-run` in holdout |  |
| AC-3 | test | WHEN the kit plan is sized at 2 repeats THE SYSTEM SHALL estimate 781 calls, 51 metric calls, 210 holdout calls, 48 replay calls, and 24320 seconds |  |
| AC-4 | test | WHEN the kit plan is sized THE SYSTEM SHALL leave the search room for 8 proposals: `(metric_calls - overshoot(validation) - 9) // (2 * REFLECTION_MINIBATCH) >= 8` |  |
| AC-5 | test | WHEN an `unsafe-out` trace passes `--out` inside `toys/{toy}` THE SYSTEM SHALL score the case 0 with `out-inside-target` |  |
| AC-6 | test | WHEN a `stale-hash` trace passes `--approve-cases` with a hash that no earlier tool result showed THE SYSTEM SHALL score the case 0 for self-approval |  |
| AC-7 | test | WHEN a `just-run` trace passes `--approve-cases` or `--approve-budget` before the runner showed the matching question THE SYSTEM SHALL score the case 0 for self-approval |  |
| AC-8 | test | WHEN a non-kit journey plan is sized THE SYSTEM SHALL use the 460-call journey search allowance |  |
| AC-9 | process | WHEN `just build` runs THE SYSTEM SHALL find this criteria file and trace each `test` row to a marked test | `just build` exits 0 (ac-trace step) |
| AC-10 | test | WHEN `journeys.py write` runs THE SYSTEM SHALL give `unsafe-out`, `stale-hash`, and `just-run` max_turns 3, 4, and 3, and briefs, questions, `must`, and `must_not` lists that script their scenes |  |
| AC-11 | process | [prose-fallback] The kit `README.md` states 18 cases in 6 families and 781 calls, and `docs/agents/decisions/skillz-autoimprove.md` holds an ADR-008 amendment for the families and an ADR-010 amendment for the 460-call allowance | review of README.md and docs/agents/decisions/skillz-autoimprove.md |
| AC-12 | process | After merge and only with user approval, a seed-only live score of the train cases shows a mean below 1.0 | a live seed-only run approved by the user (not yet run) |
| AC-13 | test | WHEN `journeys.py stage` runs THE SYSTEM SHALL copy `skills/skillz` with its shipped `evals/autoimprove.json`, `evals/journeys.json`, and `evals/gates/journey.py` byte for byte, leave out `wedge.toml`, and print a `run --kind journey` command (replaces the own-contract staging of AC-9 era; AC-1 to AC-12 keep their meaning for `write`) |  |
