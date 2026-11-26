# Autoimprove run steps

Follow these steps for approvals, execution, and reports. They replace the older run procedure.
The user needs no contract or case manifest.

## Measure the baseline

Ask for the harness and model only when the user did not name them. Do not ask for keys or tokens.
Use the installed archive. Put the run directory outside the target and keep it private.
This example uses Claude. Use the options for the selected harness on every run call.

```sh
SKILLZ=/absolute/path/to/installed/skillz
TARGET=/absolute/path/to/target-skill
MODEL=your-selected-model
OUT=$(mktemp -d)
python3 "$SKILLZ/scripts/skillz-experiment.pyz" run --target "$TARGET" --out "$OUT" --model "$MODEL" --harness claude --live
```

On `cases-missing`, draft the cases at the path in the stop. Rerun the command.
On `cases-unapproved`, show the case question, full `case_hash`, and baseline estimate in calls and seconds.
Ask one question to approve those cases and that budget together. Wait for the user's answer.
Set `CASE_HASH` to the approved hash and `BASELINE_CALLS` to the approved call count.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" run --target "$TARGET" --out "$OUT" --model "$MODEL" --harness claude --live \
  --approve-cases "$CASE_HASH" --approve-budget "$BASELINE_CALLS"
```

If only the hash is approved, `budget-unapproved` shows the same baseline estimate. Do not spend until the user approves it.
The baseline scores the seed twice on each scored train, validation, and holdout case.
The default run prints the baseline summary, writes `$OUT/report.json`, and exits 0.
It runs no search, reflection, holdout gate, or replay. Do not run `export` for a baseline-only run.
Rerunning the default command on a finished baseline prints the saved summary without model calls.

Report the family means for all three splits, the two scores for each case, and the reasons for unmet criteria.
Show saturated families, `criteria_flags`, `zero_cases`, and calls, seconds, and tokens by role.
Claim no improvement. The report exposes scored cases to its human reader. The automated proposer does not see holdout cases, scores, or reasons.

## Search only on request

Keep the same run directory and configuration. Use `--search` when the user requests a candidate patch.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" run --target "$TARGET" --out "$OUT" --model "$MODEL" --harness claude --live --search
```

On `cases-too-easy`, report the saved baseline. Do not run `export` or claim improvement.
Draft harder cases in a new run directory only when the user asks to optimize. To fix a failing criterion, edit the skill.
On `budget-unapproved`, show the search estimate and any reflection warnings. Include `estimate.reflection.seconds` when it is present.
Do not add the reflection time to the total estimate. Ask separately for search budget approval.
Set `SEARCH_CALLS` to the approved search call count.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" run --target "$TARGET" --out "$OUT" --model "$MODEL" --harness claude --live --search \
  --approve-budget "$SEARCH_CALLS"
```

You can pass `--search` on a new run. It still asks for the baseline approval first.
The call that measures the baseline cannot also approve search, regardless of the amount.
Only the separate search approval starts the search, gate, and replay.
Run `export` only after phase `complete`. Report the `runner`, `export`, and `improvement` claims with their status and evidence.
Apply a winner only when `claims.apply` is true and the user approves it.
