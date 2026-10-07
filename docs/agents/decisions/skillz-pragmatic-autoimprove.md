# skillz pragmatic autoimprove decisions

These records explain the redesign of `/skillz autoimprove` into a pragmatic GEPA flow.
The spec is `specs/skillz-pragmatic-autoimprove.md` in the durable cheese corpus.
The user settled each decision in a Mold session on 2026-10-06.
Evidence lives in `research/gepa-skill-optimizers-headless-cli/` in the same corpus, including `prototypes.md`.

## Records

### ADR-001: Reuse the existing Claude login and fail closed without a sandbox  [status: accepted]

- **Context:** The Claude transport isolates `HOME` and forwards only token variables, so a Linux user must set a token. The Codex transport already links `auth.json`. Prototype pc-2 showed that `CLAUDE_CONFIG_DIR` with a symlinked `.credentials.json` logs in with no key. The prototype host could not run the Claude Bash sandbox because of the AppArmor user-namespace restriction and a missing `socat`.
- **Decision:** On Linux, set `CLAUDE_CONFIG_DIR` to a temporary directory outside the workspace that holds only a symlink to `.credentials.json`. On macOS, use the real config directory with `--restricted`, which ignores user settings files. The preflight stops a run before any model call when `CLAUDE.md` or a nonempty `rules` directory exists there. The live preflight stops a run when user skills, plugins, agents, or MCP servers load. Forward no token variable.
  When the run closes, a link that still resolves to a replaced real file gives the warning `credential-rotated`. When Claude Code replaced the link with a refreshed login and the real file is unchanged, copy the refreshed file back with mode 0600 and give the warning `credential-refreshed`. Stop with `credential-changed` in every other case. Both warnings keep the run resumable.
  Stop with `sandbox-unavailable` and a fix hint when the Bash sandbox cannot start.
- **Alternatives:** A token from `claude setup-token`. A copied credential file. An exported Keychain item on macOS. A weaker nested sandbox. A model-visible memory canary on macOS. A close that never writes the real login and stops the run when the link is replaced.
- **Consequences:** No key setup on Linux. This record amends ADR-004 in `skillz-autoimprove.md`, which rejected copied OAuth credentials. The copy-back writes the user's login file, but only with a file that Claude Code itself refreshed. A macOS user with a global `CLAUDE.md` must move it out for a run. macOS behavior needs a test on a real Mac.

### ADR-002: Gate on a separate holdout with a clustered paired delta above 2*SE  [status: accepted]

- **Context:** Prototype pc-1 simulated five gates at 4 to 20 cases. Selecting and gating on one holdout, as skill-creator does, promoted noise 67 to 81% of the time with five candidates. Strict gates kept false promotion at or below about 6%.
- **Decision:** Select on validation. Score baseline and winner on a holdout that no selection or reflection step saw, with 3 repeats per case. Promote only when the case-clustered paired mean delta exceeds 2*SE. When SE is 0, promote only when every case delta is positive. "Measurably beats" skill-creator means a lower false-promotion rate at equal budget, with sealed-test accuracy no worse.
- **Alternatives:** A paired bootstrap interval (same power, slightly permissive at 6 cases). Mean delta above 0. The skill-creator rule.
- **Consequences:** Small runs often report `inconclusive`. This record replaces the bootstrap in skillz-repeat-statistics curd B.

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

### ADR-005: Show a preflight estimate and ask once before a larger run  [status: accepted]

- **Context:** A strict gate with repeats needs about 120 to 180 calls. The hard cap is 40 calls. Anthropic's terms assume ordinary, individual use of Claude Code.
- **Decision:** Show estimated calls and seconds before any model call and ask once. Permit up to 200 calls and 7200 seconds after approval. Run at most 2 model calls at once. Reserve the holdout gate's calls and its estimated time: the search stops when only the reserved time is left.
- **Alternatives:** A quick default tier that fits about 60 calls. Keep the 40-call cap. A fresh gate deadline on resume.
- **Consequences:** This record replaces the budget part of skillz-repeat-statistics curd A. A slow search ends early, so a run reaches a gate verdict before the deadline.

_Source: Mold session 2026-10-06 · Updated: 2026-10-07 (ADR-001 credential and macOS memory rules, ADR-005 time reserve)_
