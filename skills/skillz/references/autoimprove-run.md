# Autoimprove run steps

The `autoimprove` mode uses these steps after the experiments reference.

The agent drafts the cases. The user needs no contract and no case manifest.
Run `scripts/skillz-experiment.pyz run`, answer each coded stop, and then run `export`.
Ask at most three questions: harness and model, case approval, and budget approval.
Report the `runner`, `export`, and `improvement` claims, each with its status and evidence. Apply a winner only when `claims.apply` is true.
Use the installed `scripts/skillz-experiment.pyz`. Do not require a source checkout.
