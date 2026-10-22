# Live smoke runs

The repository tests fake every model call.
The `just live-smoke HARNESS MODEL [SCENARIO]` recipe runs the real stack from a source checkout: harness, sandbox, GEPA reflection, gate, and export.
It runs only when `SKILLZ_LIVE=1` is set. `just build` and `just ci` never set it, and the gate removes it from each step.

```sh
SKILLZ_LIVE=1 just live-smoke codex gpt-6-luna
SKILLZ_LIVE=1 SKILLZ_LIVE_DRY=1 just live-smoke codex gpt-6-luna all
```

Each run scenario runs `self-test --preflight-only`, then one `run` of a fixture skill with 12 fixed cases and `--repeats 1`, then `export`.
Each fixture seed carries one fixable flaw, so the search can beat it. A completed run must show scored holdout cases.
With `SKILLZ_LIVE_DRY=1`, each run scenario stops at its budget question, records the estimate, and makes no model call.
`SCENARIO` is `basic` (the default), `all`, or a comma list of these names:

| Scenario | What it runs | Run cap |
| --- | --- | --- |
| `basic` | Built-in harness, no contract, default judge | 97 |
| `command-harness` | `--harness command` through a test-only bridge to `codex exec` | 97 |
| `command-grader` | Contract with a `command` grader, and one `source: session` case | 53 |
| `hybrid-mixed` | `--harness-config` with a Codex task role and a command judge role, a `hybrid` grader, contract `statistics`, and `--min-gain` | 97 |
| `prose-cli` | `--edit prose+cli` with a helper script and an `exact-json` grader | 53 |
| `journey` | Journey cases. Only `claude` holds a conversation, so `codex` must stop with `journey-unsupported` | 229 |
| `resume` | `basic`, stopped with SIGINT after 3 outcomes, then the same command again | 97 |
| `fromargs-benefit` | `/skillz autoimprove` drafts the cases in `claude -p`, then a `--edit prose+cli` run of the `semver-next` fixture, which applies nine version rules by hand. See below | 103 |
| `analytics` | The bundled `agent-session-analytics.pyz`: a forced ingest of the real session logs into a private database, then every query of the three Usage packs | no model call |

A run approves its estimate only when the estimate is at most its cap. The preflight gets at most 3 more calls.
The `command-harness` and `hybrid-mixed` scenarios need `codex` and skip with another `HARNESS`.
A test passes when the run reaches `phase == complete`, the export writes `candidate.patch`, and the gate scores holdout cases.
A test also passes when a step after the preflight stops with a coded error.
An unexpected preflight stop does not pass: the preflight checks the host and the sandbox, and the run makes no task call.
A scenario that expects a stop code passes only with that code, at any stage.
The `resume` scenario must interrupt its first process with a nonzero exit before `phase == complete`.
When it stops before the interrupt, the failure message or the record shows the stage and the stop code.
Any other failure fails the test. The `skillz-pytest` gate step checks this pass rule offline, without a model call.
Each attempt appends one JSON line to `.live-smoke/runs.jsonl`: scenario, harness, model, host OS, revision, stage, verdict, code, calls, and seconds.
Git ignores that folder.

## fromargs-benefit

This scenario tests the claim that autoimprove can write its own eval and turn hand-applied rules into a fromargs CLI.
`MODEL` sets the task role. Use `sonnet`: `haiku` failed the isolation preflight in 2 of 2 runs.
`SKILLZ_LIVE_STRONG_MODEL` sets the drafting agent and the reflection role. The default is `opus`.
A sonnet reflector made only prose edits in 5 of 5 proposals and never tried `@new-cli`. Its feedback holds scores, not criterion reasons or token counts.

```sh
SKILLZ_LIVE=1 just live-smoke claude sonnet fromargs-benefit
```

1. The test builds `semver-next` in a fresh git repository: a uv project with a lock, a vendored fromargs, and one wedge target, `version-parse`. The run therefore offers `@new-cli`.
   The skill holds an approved contract, `evals/autoimprove.json`, that grades the `task` kind with `exact-json`. One evaluation costs one call, with no judge call.
   The seed `SKILL.md` says that no helper applies the commit rules yet. A skill author writes such a gap note, and it points the reflector at a CLI.
2. Stage 1 runs `/skillz autoimprove` in `claude -p` with project settings only and no MCP server. The agent stops at the case approval question.
   `semver_oracle.py` must agree with the `bump` and `next` of every drafted `expected`. A wrong answer stops the test at stage `author` with `draft-wrong`.
   Each `expected` holds exactly `bump` and `next`, because `exact-json` compares the whole answer.
   The agent drafts 24 cases in 8 families. The search budget is 4 evaluations per train and validation case, so the larger draft buys about 18 proposals.
   Each `commits.txt` must hold at least 80 commits, so that applying the rules by hand costs the seed many tokens.
3. Stage 2 runs the shared path with the agent's draft: preflight, `run --edit prose+cli --repeats 1 --min-token-saving 0.3 --max-quality-loss 0.05`, and `export`.
   A sonnet seed answers every case exactly, so the CLI can win only by the token rule (`promote-cheaper`). With `min_token_saving` set, the run does not stop with `cases-too-easy`.
4. The test applies the patch to a copy, rebuilds the new target with `wedge.pyz`, and runs it on every drafted case. It reads the command from the winner `SKILL.md`.

The test passes only when the run completes and the exported fromargs CLI matches the oracle on every case.
A coded stop fails this scenario. The record keeps the gate verdict, but the verdict does not decide the result.
With `SKILLZ_LIVE_DRY=1`, stage 1 is skipped, and the run uses the 24-case reference draft from the oracle.
The drafting session is outside the run cap. The record holds its `author_turns` and `author_cost_usd`.
