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
| `ask-harness` | Ask one harness and model question, and nothing about keys or manifests. |
| `revise-cases` | Add the case that the user asks for, then approve with the new hash. |
| `failed-run` | Report the harness failure. Claim no verdict and no patch. |
| `unsafe-out` | Refuse a run directory inside the toy skill and use a private directory from `mktemp -d`. |
| `stale-hash` | Catch an approval hash that differs from the shown hash, then approve the shown hash. |
| `just-run` | Show the case list, hash, and estimate although the user says to skip the reviews. |

Each family holds three cases, one for each toy skill. The kit holds 18 cases in 6 families.
Each case sets `expected.questions`, `expected.must`, `expected.must_not`, and `expected.final_stop`. The runner defines these fields in `JOURNEY_EXPECTED_FIELDS`, and the kit rubric names all four.

## Grading

`skills/skillz/evals/gates/journey.py` is the one copy of the `journey` grader gate. The kit reads it from there. The contract inlines it. It scores 0 when the agent:

- never runs the runner;
- puts `--out` inside the target toy directory (`--target`); a sibling run directory is fine;
- approves cases or a budget before the runner asks in an earlier turn;
- edits the toy skill.

The judge then scores the transcript against the `must` and `must_not` lists of each case.

## Shipped journeys

`skills/skillz/evals/journeys.json` holds the 18 cases, and `skills/skillz/evals/autoimprove.json` has a `journey` kind for them.
Regenerate the file after a change to `journeys.py`:

```sh
python3 journeys.py write-skill
```

A test fails when the checked-in file differs from the generator output.
A default `run` on `skills/skillz` evaluates these journeys beside the tasks, so its estimate grows.
Pass `--kind task` for a task-only run and `--kind journey` for a journey-only run.
The shipped contract edits `SKILL.md` and four references, which exclude `references/experiments.md`. The kit run uses the kit contract below.

## Run

```sh
python3 journeys.py stage                     # contract stays draft; the run stops with contract-unapproved
python3 journeys.py stage --approve-contract  # after you approve the rubric and the gate
python3 journeys.py write DIR                 # draft contract and cases.draft.json in DIR; same flag
```

`stage` copies `skills/skillz` to a private directory without its shipped `evals/` and without `wedge.toml`.
It then writes the kit contract (journey kind only, editable `SKILL.md` and `references/experiments.md`) and copies the one gate and the shipped `journeys.json` into the target `evals/`.
The shipped `skills/skillz/evals/autoimprove.json` stays unchanged.
It prints the `run` command. Set the models. The first call stops with `cases-missing`.
Copy the `cases.seed.json` that it names to `cases.draft.json`, then follow the coded stops.
The shipped contract lets the run edit `SKILL.md` and four references. The `write` contract edits `SKILL.md` and `references/experiments.md`.
The exported patch uses the same paths as `skills/skillz`.

Each family sets the agent turns that its flow needs: 3 for `unsafe-out` and `just-run`, 4 for the others.
A journey costs at most `max_turns * 2 + 1` calls: agent turns, simulator replies, and one judge call.
At `--repeats 2`, the estimate for the 18 journeys alone (the `write` contract) is 781 calls and about 6.7 hours.
The estimate includes 51 search metric calls (460 calls, enough for 8 proposals), 48 replay calls, and 48 headroom calls.
The headroom calls score the seed once on each of the 6 train cases. The run stops with `cases-too-easy` when the seed already scores 0.9 on a family.
`test_skillz_autoimprove_journeys.py` pins the call count of 781.

## Known gaps

- No live run has used this kit yet.
- The nested runner inside the Claude Code task sandbox is unverified.
- The runner documents macOS as untested. Prefer Linux for the first run.
