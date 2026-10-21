# Draft the case criteria

Each judged case needs `expected.criteria`. The runner stops with `case-criteria-missing` when a judged case has none.
This reference gives the steps to draft them. It needs only the files that `skills/skillz` bundles.

## The criterion shape

`expected.criteria` is a list of objects with these fields:

- `id`: a unique name in the case.
- `text`: one EARS statement. It has at most one leading `WHEN`, `WHILE`, `IF`, or `WHERE` clause, then `THE <subject> SHALL <response>`.
- `must`: `true` for a dealbreaker. The default is `false`.
- `from`: an optional source item, such as `assertion:2`, `must:0`, `must_not:1`, `questions`, `final_stop`, or `expected_output`.

The runner stops with `case-criteria-invalid` when a `text` is not EARS or an `id` repeats. The stop names the case and the criterion.

```json
{
  "id": "asks-before-edit",
  "text": "WHEN the request names a file that does not exist THE skill SHALL ask the user for the path before it edits.",
  "must": true,
  "from": "assertion:2"
}
```

## Steps

1. **Convert the source fields.** Turn each `assertions` item, the `expected_output`, and each journey `must`, `must_not`, `questions`, and `final_stop` item into a criterion.
   Set `from` to the source item. Keep the source fields verbatim. Do not delete or reword them.
2. **Read the skill files.** Read `SKILL.md` and each reference that it routes to. Add a criterion for each rule that a case can refute.
3. **Mine past sessions for friction.** Follow the session-mining steps in the `Check the files offline` section of the experiments reference.
   Turn each correction, retry, or failed tool call into a criterion.
4. **Run an optional research pass.** If `/briesearch` is installed, you can use it to find how others judge this kind of skill.
   Without it, skip this step. The other steps do not depend on it.
5. **List the forks.** A fork is a design choice that changes the criteria. An example is a strict or a loose reading of a rule.
   Show the fork list to the user. Offer to explore the forks in a fork session. If `/mold` is installed, you can use it for that session.
6. **Record deferred picks as agent-decided.** When the user defers a fork, pick one option. Mark the pick `agent-decided` when you show the approval question to the user.
7. **Re-offer later forks.** A fork can appear after the first offer, for example while you read the skill files. Offer each new fork to the user before you finish the draft.
8. **Read the last run's `criteria_flags`.** When an earlier run has a `report.json`, read its `criteria_flags`.
   An entry has `criterion`, `reason`, and `case_id`, because a criterion `id` is unique only inside one case.
   A `spread` entry also has `arm` and `levels`: the level spreads by 2 or more across repeats of one arm and case.
   A `partly-met` entry also has `level_2` and `outcomes`: level 2 in at least half of the case outcomes for the criterion.
   Reword or split each flagged criterion. When session analytics is available, you can also use `/session-analytics` to find the cause.
9. **Mark `must` only for dealbreakers.** A `must` criterion at level 1 or 2 scores the case 0. Leave `must` false for every other criterion.
   Keep each criterion to one EARS statement. Split a statement that holds two requirements.
