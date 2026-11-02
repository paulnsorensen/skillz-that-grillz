# autoimprove-useful-answer: a stopped baseline answers a diagnosis, and a missing fixture stops before a call

Source: reconstructed from the autoimprove-run-latency handoff (Wheypoint `paulnsorensen-dotfiles/autoimprove-run-latency@rev-d1d27ad427e5`, 2026-11-01)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN the headroom pass stops a run with `cases-too-easy` THE SYSTEM SHALL raise a `Stop` with the same code and text, whose data holds `baseline` with `search`, `holdout_gate`, and `replay` as `not-run` and `export` as `unavailable`, and SHALL make no search, reflection, or holdout call |  |
| AC-2 | test | WHEN the stop reports the baseline THE SYSTEM SHALL list each seed outcome that scores below 1 or fails, with its case, split, family, repeat, score, and the redacted reason of each unmet criterion, and SHALL show the spent `calls`, `retries`, `calls_by_role`, and `seconds_by_role` apart from `approved_calls` and the `estimate`, with the scored and unscored holdout and pending counts |  |
| AC-3 | test | WHEN the CLI resumes a `cases-too-easy` run THE SYSTEM SHALL print the same stop data on stdout with no session and no subprocess, `export` SHALL still refuse the incomplete run and write nothing, and a completed run SHALL still export |  |
| AC-4 | test | WHEN a case request names a relative path in a directory where a draft case stages files, and its own case does not stage that path, THE SYSTEM SHALL stop `run` with `case-fixture-missing` before the case question and any model call, and `validate` SHALL report the same code |  |
| AC-5 | test | WHEN a request names no file, or names an absolute path, a URL, or a path outside the staged fixture directories, THE SYSTEM SHALL accept the case, so a request need not name each auxiliary file |  |
| AC-6 | process | WHEN an agent reads the autoimprove run steps THE SYSTEM SHALL tell it to run `export` only after phase `complete`, and on `cases-too-easy` to report `baseline` and not to draft harder cases unless the user asks to optimize | review of `skills/skillz/references/autoimprove-run.md` and `experiments.md` in this change |
