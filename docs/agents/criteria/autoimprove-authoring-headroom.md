# autoimprove-authoring-headroom: stop a run when the seed already saturates a family

Source: autoimprove-authoring-headroom (Mold spec in the durable cheese corpus; curd C1 headroom-stop)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN `Session.search` starts after the budget approval THE SYSTEM SHALL call `Session.headroom()` before the engine, score the seed once on every train and validation case, and record the outcomes so that `_reuse` serves each one to the search, with no new call for the seed validation pass of GEPA |  |
| AC-2 | test | WHEN a train or validation family has a seed mean of at least 0.9 THE SYSTEM SHALL stop with `cases-too-easy`, name each such family with its split and mean, record `headroom.verdict: too-easy` in `run.json`, and make no search call |  |
| AC-3 | test | WHEN every train and validation family has a seed mean below 0.9 THE SYSTEM SHALL record `headroom.verdict: ok` and continue to the search with the `metric_calls` that the plan approved, because the search ceiling includes the headroom train outcomes |  |
| AC-4 | test | WHEN the resolved statistics set `min_token_saving` THE SYSTEM SHALL record the headroom scores and SHALL NOT stop with `cases-too-easy` |  |
| AC-5 | test | WHEN the plan builds the budget estimate THE SYSTEM SHALL show the headroom calls on their own line, charged at the train case cost, and include them in the approved total, the ceilings, and the search ceiling |  |
| AC-6 | test | WHEN the user runs the built `skillz-experiment.pyz scaffold --target SKILL` on a skill with no contract THE SYSTEM SHALL write `evals/autoimprove.json` with `status: draft`, a `journey` kind whose argv inlines `_journey_gate.py`, and a rubric, and make no model call; the file SHALL load with `load_contract` except for the `contract-unapproved` stop |  |
| AC-7 | test | WHEN `scaffold` finds an existing `evals/autoimprove.json` THE SYSTEM SHALL stop with `contract-exists` and leave the file unchanged |  |
| AC-8 | test | WHEN the generic journey gate runs THE SYSTEM SHALL print score 0 with `no-trace` when `trace/events.jsonl` is absent, score 0 with `no-agent-turn` when no event has `type: assistant`, and score 1 otherwise |  |
| AC-9 | test | WHEN an agent reads `experiments.md` THE SYSTEM SHALL describe, in a `## Draft journeys` section, how to write `brief`, `max_turns`, `requirements`, `expected.must`, `expected.must_not`, and `expected.questions`, how to run `scaffold`, and how a `cases-too-easy` stop leads to harder cases, and SHALL no longer say that journeys stay hand-authored |  |
| AC-10 | test | WHEN analytics exists THE SYSTEM SHALL tell the agent, in `## Draft journeys`, to select target-skill sessions with a user correction, a retry, a failed tool call, or a long multi-turn exchange, to draft `source: session` cases from the request before the solution, to redact home paths, emails, hostnames, and tokens, and never to use the session's solution as `expected` |  |
| AC-11 | process | WHEN the docs change THE SYSTEM SHALL keep at most seven concepts and at most three user questions in `experiments.md` | review of `skills/skillz/references/experiments.md` (Budget and Run text) |
| AC-12 | test | WHEN a run resumes after its headroom pass THE SYSTEM SHALL score no seed case again, and a run that stopped with `cases-too-easy` SHALL return the same stop from `run.json` with no model call |  |
