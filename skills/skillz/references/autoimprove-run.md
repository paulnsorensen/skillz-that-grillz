# Autoimprove run steps

The `autoimprove` mode uses these steps after the experiments reference.

The agent drafts the cases. The user needs no contract and no case manifest.
Run `scripts/skillz-experiment.pyz run`. Answer each coded stop. Run `export` only after the run reports phase `complete`.
Ask at most three questions: harness and model, case approval, and budget approval.
Report the `runner`, `export`, and `improvement` claims, each with its status and evidence. Apply a winner only when `claims.apply` is true.
When the run stops with `cases-too-easy`, no search, holdout gate, or export runs. Do not call `export`.
Report the stop's `baseline`: the `failing` criteria, the spent calls, and the `unscored` holdout and pending cases. Claim no improvement.
Draft harder cases in a new run directory only when the user asks to optimize the skill. To fix a failing criterion, edit the skill.
Use the installed `scripts/skillz-experiment.pyz`. Do not require a source checkout.
