# fromargs-benefit-smoke: autoimprove writes its own eval and ships a fromargs CLI

Source: reconstructed from the design session of 2026-10-20 (fixture `semver-next`, pass bar "fromargs CLI exported", claude harness with a haiku task role and about 100 calls)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN the oracle reads a `VERSION` and a `commits.txt`, THE SYSTEM SHALL apply the nine rules of the `semver-next` fixture `SKILL.md`, and give the answer of its example. |  |
| AC-2 | test | WHEN the stage 1 agent writes the case draft, THE SYSTEM SHALL refuse a task case whose `expected` differs from the oracle, that lacks `VERSION` or `commits.txt`, whose `commits.txt` holds fewer than 80 commits, or whose `expected` holds keys other than exactly `bump` and `next`, and a draft whose task cases span fewer than 3 families. The scenario contract SHALL grade the `task` kind with `exact-json`. |  |
| AC-3 | test | WHEN the exported CLI answers a task case differently from the oracle, or the winner `SKILL.md` names no command that runs it, THE SYSTEM SHALL record `cli` as other than `ok`. |  |
| AC-4 | test | WHEN a `fromargs-benefit` record lacks a completed run, an exported `candidate.patch`, or `cli == ok`, THE SYSTEM SHALL fail the test whatever the gate verdict, and name the stage and the stop code of a stopped run. |  |
| AC-5 | process | WHEN `SKILLZ_LIVE_DRY=1 just live-smoke claude sonnet fromargs-benefit` runs, THE SYSTEM SHALL build the fixture, offer `@new-cli`, and stop at the budget question with no model call. | Dry run on 2026-10-20: stage `budget`, estimate 103 calls, holdout 6 cases; `_capability` returned a fromargs capability for the built fixture. Dry run on 2026-10-22 at 298536d (sonnet task role): stage `budget`, estimate 103 calls. |
