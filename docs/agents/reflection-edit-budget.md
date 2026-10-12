# Reflection edit budget and run status

Each reflection prompt in the skillz GEPA search states an edit budget and a run-status block.
The budget keeps late proposals small, and the status block tells the proposer where the run stands.
The code is `Progress` in `packages/skillz/src/skillz_experiments/search/_proposer.py`.
The operator reference is `### Edit budget and run status` in `skills/skillz/references/experiments.md`.
This came from W4.2 (issue #239, PR #273). [[decisions/skillz-autoimprove]] ADR-016 records why progress uses the search allowance.

## Progress p

`p` is the share of the search that is spent, from 0 to 1.
The session computes it again before each reflection prompt.

```text
p = spent / allowance, clamped to [0, 1]
allowance = estimate.metric_calls - overshoot(len(validation))
overshoot(validation) = 2 * REFLECTION_MINIBATCH + validation = 4 + validation
```

- `spent` is the count of search metric calls in the run record. Reflection calls do not count.
- The allowance is the GEPA call budget. GEPA checks its budget only between iterations, so the last iteration can pass the allowance by up to the overshoot margin.
- An allowance below 1 counts as fully spent, so p is 1 from the first proposal.

## What p controls

- **Edit budget:** `b(p) = floor(1 + 5 * 0.5 * (1 + cos(pi * p)))`. It falls from 6 edits at p = 0 to 1 edit at p = 1.
- **Last-proposal sentence:** from p = 0.85 the prompt adds "This may be the last proposal, so prefer one small, safe change."
- **Status line:** "this is proposal K. Search calls spent: X of N (P%)." N is the allowance. P uses integer math, rounded down, and stops at 100.

An edit is one block of consecutive changed lines, from `difflib.SequenceMatcher` over the lines of one component.
The budget is advisory. The runner never rejects a proposal for its size.
It counts each proposal above its stated budget in `search.proposal_over_budget`.
That count decides later whether enforcement is worth the wasted reflection calls (D-3 of #239).

## Worked example

In the PR #273 review probe, a run with 72 planned calls ended its search at 58 calls.
The example takes the allowance as 58, which is an overshoot of 14 (10 validation cases).

| spent | p | budget | last-proposal sentence |
| --- | --- | --- | --- |
| 0 | 0.00 | 6 | no |
| 15 | 0.26 | 5 | no |
| 29 | 0.50 | 3 | no |
| 44 | 0.76 | 1 | no |
| 50 | 0.86 | 1 | yes |
| 58 | 1.00 | 1 | yes |

## Why not divide by the planned calls

The first W4.2 implementation used `p = spent / estimate.metric_calls`.
The search stops near the allowance, so p never reached 1.

| Run | Search ends at | Old p at the end | Old final budget | New p at the end | New final budget |
| --- | --- | --- | --- | --- | --- |
| 72 planned, allowance 58 | 58 calls | 0.81 | 1 | 1.00 | 1 |
| 16 planned, allowance 10 | 10 calls | 0.62 | 2 | 1.00 | 1 |

Under the old formula the last-proposal sentence never appeared in a real run, and a small run never reached budget 1.
The end point differs for each run size, so a lower fixed threshold does not fix the old formula.

## Gotchas

- A small run goes through the whole 6-to-1 schedule in few calls, so its budget falls fast.
- The N in "X of N" differs from `estimate.metric_calls` in `run.json`. That is expected.
- The prompt text is part of the frozen experiment inputs. A change to it starts a new run, and an older run cannot resume.
