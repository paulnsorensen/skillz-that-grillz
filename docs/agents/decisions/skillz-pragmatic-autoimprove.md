# skillz pragmatic autoimprove decisions

These records explain the redesign of `/skillz autoimprove` into a pragmatic GEPA flow.
The spec is `specs/skillz-pragmatic-autoimprove.md` in the durable cheese corpus.
The user settled each decision in a Mold session on 2026-10-06.
Evidence lives in `research/gepa-skill-optimizers-headless-cli/` in the same corpus, including `prototypes.md`.

## Records

### ADR-001: Reuse the existing Claude login and fail closed without a sandbox  [status: superseded in part by skillz-autoimprove ADR-006; amended 2026-10-23 for gh-131]

- **Context:** The Claude transport isolates `HOME` and forwards only token variables, so a Linux user must set a token. The Codex transport already links `auth.json`. Prototype pc-2 showed that `CLAUDE_CONFIG_DIR` with a symlinked `.credentials.json` logs in with no key. The prototype host could not run the Claude Bash sandbox because of the AppArmor user-namespace restriction and a missing `socat`.
- **Decision:** On Linux, set `CLAUDE_CONFIG_DIR` to a temporary directory outside the workspace that holds only a symlink to `.credentials.json`. On macOS, use the real config directory with `--restricted`, which ignores user settings files. The preflight stops a run before any model call when `CLAUDE.md` or a `rules` directory with `.md` files exists there. The live preflight stops a run when user skills, plugins, agents, or MCP servers load. Forward no token variable.
  All Claude roles of one run share one temporary directory and one login link. The run closes the link once, after every role closes.
  A refreshed login file has an OAuth entry with a nonempty access token and refresh token, and the same top-level keys as the real file.
  When the link still resolves to a replaced real file that is a login file, give the warning `credential-rotated`.
  Claude Code can replace the link with a refreshed login file. When the real file is unchanged, copy that file back with mode 0600.
  Then give the warning `credential-refreshed`. Stop with `credential-changed` in every other case. Both warnings keep the run resumable.
  Stop with `sandbox-unavailable` and a fix hint when the Bash sandbox cannot start.
- **Alternatives:** A token from `claude setup-token`. A copied credential file. An exported Keychain item on macOS. A weaker nested sandbox. A model-visible memory canary on macOS. A close that never writes the real login and stops the run when the link is replaced.
- **Consequences:** No key setup on Linux. This record amends ADR-004 in `skillz-autoimprove.md`, which rejected copied OAuth credentials. The copy-back writes the user's login file, but only with a refreshed login file. Concurrent roles can each refresh the login in the shared directory, and the last write wins. A macOS user with a global `CLAUDE.md` must move it out for a run. macOS behavior needs a test on a real Mac.
- **Amendment 2026-10-23 (issue #131):** A live macOS run found three faults. Each one stops every macOS run before any useful call.
  - Claude Code keys the Keychain login to the config directory and the login user. An explicit `CLAUDE_CONFIG_DIR`, a changed `HOME`, or an unset `USER` hides the login, and the run stops with `login-missing`. The macOS child now keeps the real `HOME`, gets `USER` and `LOGNAME` from the password database, and gets `CLAUDE_CONFIG_DIR` only when the host sets it. The workspace `HOME` stays on Linux.
  - `--setting-sources project` already keeps the user `CLAUDE.md` and `rules` out (checked with `/context` on Claude Code 2.1.309). The static stop made every user with a global `CLAUDE.md` move it. When memory exists, the runner now runs `claude -p /context` once per role in each process. It is a local command, so it makes no model call. The run stops with `preflight-leak` when the listing names the memory or does not run.
  - The sandbox floor `denyRead: ["/"]` breaks on macOS: see the skillz-autoimprove ADR-004 amendment of the same date.
  - Alternatives: a `claude setup-token` token, and a copied Keychain item. Both need user setup or put a secret in a file, so they are rejected.
  - Consequence: no user setup on macOS. The child can read the real `~/.claude.json`. `--strict-mcp-config` and the live inventory still stop user MCP servers, skills, plugins, and agents. Account connectors of a claude.ai login can still load; this gap is open.

### ADR-002: Gate on a separate holdout with a clustered paired delta above 2*SE  [status: accepted]

- **Context:** Prototype pc-1 simulated five gates at 4 to 20 cases. Selecting and gating on one holdout, as skill-creator does, promoted noise 67 to 81% of the time with five candidates. Strict gates kept false promotion at or below about 6%.
- **Decision:** Select on validation. Score baseline and winner on a holdout that no selection or reflection step saw, with 3 repeats per case. Promote only when the case-clustered paired mean delta exceeds 2*SE. When SE is 0, promote only when every case delta is positive. "Measurably beats" skill-creator means a lower false-promotion rate at equal budget, with sealed-test accuracy no worse.
- **Alternatives:** A paired bootstrap interval (same power, slightly permissive at 6 cases). Mean delta above 0. The skill-creator rule.
- **Consequences:** Small runs often report `inconclusive`. This record replaces the bootstrap in skillz-repeat-statistics curd B.
  Amended 2026-10-09: `min_token_saving` adds `promote-cheaper`, a separate opt-in verdict. It needs a mean delta of at least 0 and claims no gain. The 2*SE rule for `promote` stays.
  Bound added 2026-10-13: `promote-cheaper` also needs `max_quality_loss` and `delta - 2*SE >= -max_quality_loss`. A null margin turns it off, and `min_token_saving` without a margin stops the run. `self-test --simulate` measures the rate of the production gate under a true loss of 0.05 per case, half the tokens, and `min_token_saving` 0.3 (seed 20261006, 2000 trials, 10 cases): 0.0005 at margin 0, 0.0035 at margin 0.02, 0.016 at margin 0.05. With no true loss and margin 0.05 the rate is 0.0285, so the verdict still fires. All stay below the `promote` target of 0.06. Without the margin check the rate was 0.346. `skillz-self-update` applies a patch only on `promote`. On `promote-cheaper` it reports the result and does not apply the patch.
  Amendment 2026-10-24 (gh-529): the margin check uses the SE of the case losses, `se_loss = max(stdev(min(delta_i, 0)) / sqrt(n), se_floor)`, in place of the SE of all deltas: `delta >= 0` and `delta - 2*se_loss >= -max_quality_loss`. A case that the candidate fixes outright widened the full SE, so a winner with no loss and one fixed case missed the margin, as in a live run with eight deltas of 0 and one of +0.5. A mean of `min(delta_i, 0)` does not work: noise biases it negative, and it gave 0% `promote-cheaper` on every noisy scenario. The same simulation (seed 20261006, 2000 trials, 10 cases, 3 repeats) gives these rates, old to new: true loss 0.05 at margin 0.05, 0.016 to 0.024; true loss 0.05 at margin 0, 0.0005 to 0.006; true loss 0.05 at margin 0.02, 0.0035 to 0.009; true loss 0.10 at margin 0.05, 0.0065 to 0.010; no loss at margin 0.05, 0.0285 to 0.0465; no loss and one case fixed outright (baseline chance 0.5, candidate 1.0) at margin 0.05, 0.0575 to 0.0995. The loss rates stay below the `promote` target of 0.06; the two no-loss rates are power, not false promotion. `self-test --simulate` reports the last scenario as `cheaper_loss_0_margin_5_fixed_1`.

### ADR-003: Run one multi-iteration GEPA search with Pareto parent selection  [status: accepted]

- **Context:** Today each arm makes one proposal with `max_metric_calls=5`. GEPA picks the final candidate by mean validation score; Pareto selection only picks the parent to mutate. The GEPA paper reports +12.4 for Pareto selection against +6.1 for greedy selection.
- **Decision:** Run one search with several iterations, Pareto parent selection, and a minibatch pre-gate. Prose is the default editable set. Prose plus helper scripts is an option. Remove `dataset`, `baseline`, `search --mode`, `evaluate`, and the `cli`-only and `wedge` modes; a removed command names `run` as its replacement.
- **Alternatives:** Greedy `current_best` selection. One proposal. Three fixed arms.
- **Consequences:** A breaking CLI change and a `run.json` schema bump. Pareto selection on 6 to 10 validation cases is untested.

### ADR-004: Draft cases with the agent and from sessions, then ask once  [status: accepted]

- **Context:** A first run needs a hand-written contract and case manifest. skill-creator drafts about 20 trigger queries for one approval.
- **Decision:** The agent drafts trigger and task cases from the target skill. When analytics exists, it also drafts cases from past sessions. Code assigns splits from a recorded seed so that no family crosses splits. The user approves all cases in one question.
- **Alternatives:** Hand-written cases only. Agent drafts without session mining.
- **Consequences:** Session mining waits on issue #132 and #108 item 4.

### ADR-005: Show a preflight estimate and ask once before a larger run  [status: accepted; amended by skillz-autoimprove ADR-010]

- **Context:** A strict gate with repeats needs about 120 to 180 calls. The hard cap is 40 calls. Anthropic's terms assume ordinary, individual use of Claude Code.
- **Decision:** Show estimated calls and seconds before any model call and ask once. Permit up to 200 calls and 7200 seconds after approval. Run at most 2 model calls at once. Reserve the holdout gate's calls and its estimated time: the search stops when only the reserved time is left.
- **Alternatives:** A quick default tier that fits about 60 calls. Keep the 40-call cap. A fresh gate deadline on resume.
- **Consequences:** This record replaces the budget part of skillz-repeat-statistics curd A. A slow search ends early, so a run reaches a gate verdict before the deadline. The gate time reservation uses 60 seconds for each call, so slower gate calls can still reach the deadline.
  On resume, the elapsed time is the larger of the wall-clock span and the monotonic span. After a reboot, the monotonic span is negative, so the wall-clock span applies. Time in system suspend counts against the deadline.
  The wall clock can step back by up to 5 seconds. A larger step back stops the resume with `clock-skew`.
  - **Amendment 2026-10-30 (gh-571, two rates):** The plan estimate charges 30 seconds for each call (`ESTIMATE_SECONDS_PER_CALL`). In 22 live runs, one of them a journey run, the effective time for each call was 5.5 to 21.7 seconds. The journey run took 539.5 seconds against an estimate of 19430 seconds. The gate time reservation and the per-call timeout keep 60 seconds for each call (`SECONDS_PER_CALL`). The reservation is now capped by the seconds cap, not by the estimate, so a run with a large gate keeps its full reservation. The estimate records `seconds_per_call`; a run prepared at the 60-second rate stops on resume as an older run.

_Source: Mold session 2026-10-06 · Updated: 2026-10-30 (ADR-001 credential and macOS memory rules, ADR-002 promote-cheaper amendment and quality-loss bound, ADR-005 gate time reservation and resume clock, ADR-005 amended by skillz-autoimprove ADR-010; 2026-10-30 ADR-005 two-rates amendment, gh-571)_
