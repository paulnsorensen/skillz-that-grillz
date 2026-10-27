# skillz live proof lessons

This page records what the #324 live proof taught, so a later live run or PR does not learn it again.
The proof closed on 2026-10-27: `changelog-proof` gave a quality `promote` on Claude and Codex on `main` at `b9993984`.
The design records are in [skillz autoimprove decisions](./decisions/skillz-autoimprove.md) (ADR-006 amendments) and in the `gh-176`, `gh-300`, and `gh-301` criteria files.

## Keep live probe prompts short, plain, and split

The Claude preflight asks the model to try reads, writes, sockets, and network calls that the sandbox must refuse.
One long prompt with all these steps failed live: Sonnet refused the whole list as "a sweep of sandbox-boundary and network-egress tests" that looked like an injected instruction block.
It made no tool call, so the run stopped with `preflight-leak` and no evidence.

- Split the probe by tool. Each role now makes one Bash sandbox call and one Read file-access call (`PREFLIGHT_CALLS = 6`, `READ_PROBE` v4).
- Use plain words in model-facing text: `outside.txt`, `link.txt`, "file-access check". Do not write "sealed", "secret", or "escape".
- Keep the verdict strict. A call with no tool call of its type still stops the run. The stop reason says that the model refused or skipped the check, not that a leak occurred.
- Offline tests cannot show a model refusal. Only a live preflight proves a changed probe prompt.

## Read confinement uses the native setting, not removal of Read

`permissions.blockReadsOutsideWorkingDirectories` makes the Read tool refuse paths outside the working directories in every permission mode.
The `denyRead` list of the sandbox does not apply to the Read tool; only permission rules do.
Read stays in `TOOLS`, so evaluation behavior and the judge capture reads do not change.
An older Claude Code ignores the setting. The Read check on `/etc/hosts` then returns content, and the run stops with `preflight-leak`.

## Split floors change suites, approved cases, and caps together

The train floor (4 scored cases from 2 families after the holdout minimum of 6) stopped suites that ran before.
The fix had to enlarge the shipped `skills/skillz` task suite (8 to 12 cases, which the owner approved) and reshape the live smoke suites to 4 families.

- The seeded split stays first, so suites that already met the floors keep their case hashes. A fallback placement runs only when the seeded split misses a floor.
- Open gap: a split can still give validation one family of one case. One validation case gives one paired delta, and a promotion needs two, so such a run can never promote. A validation floor was out of scope.

## Live smoke caps equal the dry estimate

Each scenario cap in `test_live_smoke.py` is the dry estimate (`SKILLZ_LIVE_DRY=1`), not a rounded budget.
Two PRs that each move the estimate conflict on the caps. Recompute the caps on the combined tree with a dry run of both harnesses. Do not add the two deltas by hand without that check.

## A scoring label change is a cohort break

`judge.scoring_policy` is part of the frozen judge identity in `run.json`.
The rubric label `criteria-levels;must-below-3-zero;mean-level-minus-1-over-3;v1` made every earlier rubric run refuse to resume and to export.
The owner accepted this. Treat any later label change the same way, and say so in the PR.

## CPU-time ratio tests need wide margins on this host

The development host has two kinds of cores. Under the gate's parallel lanes, the CPU time of the same work changes by about 3x.
Linearity tests with a 2x margin failed now and then for this reason, not because the code was quadratic.
PR #552 moves these tests to a shared helper: a 16x size step, a limit of 64, timing that alternates the small and large inputs, the minimum of 7 rounds, and thread CPU time.
Write a new linearity test with the same helper. Prove it with a temporary quadratic variant.

## Process gotchas

- `Closes #n` in a PR body does not close the issue on merge, because the repository default branch is `dist`, not `main`. Close the issue by hand with the merge commit.
- The T3 `delegate_task` tool fails inside a subagent with `parent_not_active`. A subagent that needs the Astra advisor runs `codex exec -m gpt-6-astra -c model_reasoning_effort=high -s read-only` instead.
- Field-test combined PRs on one integration branch. Merge them only when `main` after the merges has the same tree as the branch that passed live.
