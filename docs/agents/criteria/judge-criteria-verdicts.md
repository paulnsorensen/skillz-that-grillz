# judge-criteria-verdicts: the judge grades typed case criteria, and code computes the score

Source: judge-criteria-verdicts (Mold spec in the durable cheese corpus)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-15 | test | WHEN a tool result in a skillz journey trace holds the runner `cases-unapproved` stop, and the assistant text after the last such stop of that turn lacks its 64-character `case_hash` as a hex run not inside a longer hex run of either case, or is absent, THE skillz journey gate SHALL print a score of 0 and name the failed rule |  |
| AC-1 | test | WHEN a judge, hybrid, or journey grader scores an outcome THE SYSTEM SHALL ask the judge for one level from 1 to 4 for each criterion id of the case, and SHALL reject a reply that omits an id, adds an id, or gives a level outside 1 to 4 as `invalid judge response` |  |
| AC-2 | test | WHEN a criterion with `must: true` gets level 1 or 2 THE SYSTEM SHALL score the outcome 0 |  |
| AC-3 | test | WHEN every `must` criterion gets level 3 or 4 THE SYSTEM SHALL score the outcome as the mean of (level - 1) / 3 over all criteria of the case |  |
| AC-5 | test | WHEN the judge prompt is built THE SYSTEM SHALL define the levels as 1 not met, 2 partly met, 3 mostly met, and 4 fully met |  |
| AC-6 | test | WHEN intake loads a judged case whose `expected.criteria` item is not one EARS statement, or repeats an id, THE SYSTEM SHALL stop with `case-criteria-invalid` and name the case id and the criterion id |  |
| AC-7 | test | WHEN intake loads a judged case with no `expected.criteria` THE SYSTEM SHALL stop with `case-criteria-missing`, name the case id, and point to the criteria drafting procedure; for a journey case it SHALL raise `expected-missing` and `journey-expected-invalid` first |  |
| AC-8 | test | WHEN a case carries `expected_output`, `assertions`, `must`, `must_not`, `questions`, or `final_stop` THE SYSTEM SHALL keep them unchanged, SHALL accept `criteria` as a journey `expected` key, and SHALL accept an optional `from` on each criterion |  |
| AC-9 | test | WHEN a criterion omits `must` THE SYSTEM SHALL treat it as `must: false` |  |
| AC-10 | test | WHEN the case approval question is shown THE SYSTEM SHALL list each case's criteria with their `must` flag and `from` source, and the case hash SHALL cover them |  |
| AC-14 | test | WHEN a contract kind declares `pillars` THE SYSTEM SHALL stop at contract load with `contract-pillars-removed` and say to move each pillar into case criteria, and `autoimprove.schema.json` SHALL reject `pillars` |  |
| AC-16 | test | WHEN the shipped skillz contract, the `scaffold` rubric, the default task rubric, or a journey kit contract loads THE SYSTEM SHALL find no score arithmetic and no `questions` or `final_stop` rule in its rubrics |  |
| AC-17 | test | WHEN a run frozen before this change resumes or exports THE SYSTEM SHALL refuse it as a different evaluator and say to start a new run |  |
| AC-18 | test | WHEN a journey rubric names `reference.criteria` THE SYSTEM SHALL accept it, and the judge prompt's reference-field table SHALL list `criteria` with its meaning |  |
| AC-19 | test | WHEN a judge, hybrid, or journey outcome is scored THE SYSTEM SHALL store the per-criterion verdicts as level and reason under `criteria` of the outcome, and SHALL NOT add them to `scores` |  |
| AC-20 | test | WHEN intake loads a judged case whose criterion `text` or `from` holds a control, format, surrogate, or line-separator character, or holds more than 1000 characters, THE SYSTEM SHALL stop with `case-criteria-invalid`, SHALL name the first such code point as `U+XXXX` and its position, and SHALL accept exactly 1000 characters |  |
| AC-4 | test | WHEN the judge scores an outcome THE SYSTEM SHALL store each criterion's level and one-line reason under the outcome key `criteria`, leave `scores` without them, and write them redacted in the export `report.json` |  |
| AC-11 | test | WHEN `export` runs on a run with criteria verdicts THE SYSTEM SHALL write a `criteria_flags` entry for each criterion whose level spans 2 or more across repeats of one arm and case, or that gets level 2 in at least half of its outcomes |  |
| AC-12 | test | WHEN the skill content tests run THE SYSTEM SHALL find a criteria drafting reference in `skills/skillz/references/`, named from "Draft the cases" in `experiments.md`, with one marked step each for source-field conversion, skill files, past-session friction, an optional research pass, the fork list and fork-session offer, agent-decided picks on deferral, the re-offer of later forks, the last run's `criteria_flags`, and `must` only for dealbreakers |  |
| AC-13 | test | WHEN the skill content tests run THE SYSTEM SHALL find each mention of `/mold`, `/briesearch`, or `/session-analytics` in the criteria drafting reference inside a sentence that marks it optional |  |
