# proposer-cli-early: the reflection prompt offers @new-cli early and keeps room for its two edits

Source: the `changelog-proof` live runs of 2026-10-26 at `fc73477c` (#324). On Claude and on Codex, no proposal used `@new-cli`. The proposers treated a CLI as a fallback after a prose fix fails, and the edit budget fell to 1 edit, below the 2 edits that a new target and its `SKILL.md` call need.

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN a run offers `@new-cli`, THE SYSTEM SHALL tell the proposer that a rule that code applies the same way to every input is a good fit for it, and to try it early. |  |
| AC-2 | test | WHILE a run offers `@new-cli` AND the parent candidate leaves it empty, THE SYSTEM SHALL state an edit budget of at least 2 and say that a new target and its `SKILL.md` call are 2 edits. |  |
| AC-3 | test | WHEN a run offers `@new-cli`, THE SYSTEM SHALL give the proposer a summary of the fromargs API that is enough to write a module that runs, and SHALL say that a module that uses only the standard library is valid. The Codex run at `c9455b78` wrote no target, because it could not see the fromargs API. |  |
