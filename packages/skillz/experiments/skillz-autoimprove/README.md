# Autoimprove journeys for `/skillz autoimprove`

This kit runs `/skillz autoimprove` on the skillz skill itself.
It measures the autoimprove mode: `SKILL.md` and `references/experiments.md`.
A simulator plays the user across several turns.

## Journeys

Each journey asks the agent to autoimprove one toy skill under `fixtures/toys/`.
The toy run uses `fixtures/fake_harness.py`, a stub command harness that refuses every call.
A nested run therefore makes no model call.

| Family | What the agent must do |
|---|---|
| `named-harness` | Skip the harness question, show the case question and hash, then stop when the user declines the budget. |
| `ask-harness` | Ask one harness and model question, and nothing about keys or manifests. |
| `revise-cases` | Add the case that the user asks for, then approve with the new hash. |
| `failed-run` | Report the harness failure. Claim no verdict and no patch. |

Each family holds three cases, one for each toy skill.

## Grading

`gate.py` is the `journey` grader gate. The contract inlines it. It scores 0 when the agent:

- never runs the runner;
- puts `--out` inside the target toy directory (`--target`); a sibling run directory is fine;
- approves cases or a budget before the runner asks in an earlier turn;
- edits the toy skill.

The judge then scores the transcript against the `must` and `must_not` lists of each case.

## Run

```sh
python3 journeys.py stage                     # contract stays draft; the run stops with contract-unapproved
python3 journeys.py stage --approve-contract  # after you approve the rubric and the gate
```

`stage` copies `skills/skillz` to a private directory without `evals/` and `wedge.toml`.
The shipped `skills/skillz/evals/autoimprove.json` stays unchanged.
It prints the `run` command. Set the models, then follow the coded stops.
The exported patch uses the same paths as `skills/skillz`.

Each family sets the agent turns that its flow needs: 3 for `named-harness`, 4 for the others.
A journey costs at most `max_turns * 2 + 1` calls: agent turns, simulator replies, and one judge call.
At `--repeats 2`, the estimate is 435 calls and about 3.8 hours.
`test_skillz_autoimprove_journeys.py` pins the call count of 435.

## Known gaps

- No live run has used this kit yet.
- The nested runner inside the Claude Code task sandbox is unverified.
- The runner documents macOS as untested. Prefer Linux for the first run.
