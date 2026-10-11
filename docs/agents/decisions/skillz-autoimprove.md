# skillz autoimprove decisions

These records explain the design of `/skillz autoimprove`, which generalizes the skillz experiment runner at `packages/skillz/src/skillz_experiments/`.
The approved spec is `specs/skillz-autoimprove.md` in the durable cheese corpus.
The user settled each decision in a Mold session on 2026-10-03. ADR-008 to ADR-010 come from a Mold session on 2026-10-10. ADR-011 to ADR-013 come from a second Mold session on 2026-10-10, recorded in `specs/skillz-experiments-module-layout.md`.

## Records

Skillz-pragmatic-autoimprove ADR-003 removes the `dataset`, `baseline`, `search`, and `evaluate` commands and the `cli`-only and `wedge` arms.
That ADR supersedes the records about those commands and arms in this file. These records are ADR-001 (case manifest location), ADR-002 (arm scoring), and ADR-005 (wedge arm).

### ADR-001: Declare the autoimprove contract in one schema with two locations  [status: accepted]
- **Context:** The runner hard-wires skillz: staging path, `$skillz audit` prompt, `inspect_skill.py` helper, and grader (`_codex.py:145`, `_evaluator.py:24-35`). The [GEPA research](../research/gepa-session-optimization.md) says a generic optimizer accepts a declared build and test contract.
- **Decision:** A contract names the skill, task kinds, invocation, optional helper, grader per kind, and editable files. It lives in `<skill>/evals/autoimprove.json` or in a `target` block of the case manifest; the manifest block wins. With neither, autoimprove asks the user to choose judge-only grading with a powerful model, or to find or draft a contract. A drafted contract carries `"status": "draft"` and `dataset` refuses it until the user approves it.
- **Alternatives:** Inference from SKILL.md and `scripts/` with judge-only grading (no deterministic grade for rewrite skills). A manifest-only or skill-only location.
- **Consequences:** Skills outside this repository work. Each target needs a contract, but autoimprove can draft one.
- **Amendment 2026-10-10:** The helper is now `inspect-skill.pyz`, built from `packages/skillz/src/skillz_inspect`.

### ADR-002: Use exact-json, judge, command, and hybrid graders  [status: accepted]
- **Context:** Rewrite skills such as bash-shortening produce changed files. Their truth is mostly output state. gskill grades by test pass or fail only.
- **Decision:** Four grader kinds. A `hybrid` grader runs a sandboxed `command` gate first; a failed gate scores 0 and skips the judge. Both results go into run outcomes as `scores` and into GEPA side info as `grader_scores`, because GEPA reserves the `scores` key for Pareto objectives. The command grader never sees `expected` or the rubric.
- **Alternatives:** Judge-only grading for rewrites (a judge can pass a broken rewrite). Three kinds without a hybrid.
- **Consequences:** The hybrid costs one judge invocation, the same as `audit` today.

### ADR-003: Write and audit skillz prose in ASD-STE100  [status: accepted]
- **Context:** The user requires ASD-STE100 for the skillz skill. `inspect_skill.py` reports facts only and is a frozen experiment component.
- **Decision:** Skillz docs and add, improve, and reflection prose use STE. `inspect_skill.py` schema v2 adds `long_sentences` facts. A `Prose (ASD-STE100)` audit lens judges passive voice and multi-instruction sentences.
  Schema v3 moves `long_sentences` to sentences over 25 words and adds `advisory_sentences` for 21 to 25 words.
- **Alternatives:** A judge-only lens. A separate STE script.
- **Consequences:** A helper schema bump regenerates the `HELPER_FIXTURES` outputs in `packages/skillz/src/skillz_experiments/_evaluation.py`, `skills/skillz/evals/autoimprove.json`, and the `skills/skillz/references/experiments.md` example.
- **Amendment 2026-10-10:** The inspector is now the fromargs package `packages/skillz/src/skillz_inspect`, built as `inspect-skill.pyz`, at schema 4.

### ADR-004: Run the Claude Code adapter in --restricted mode  [status: superseded in part by ADR-006; amended by skillz-pragmatic-autoimprove ADR-001]
- **Context:** The Codex adapter isolates discovery, filesystem, and network. Claude Code documents `--restricted` for eval harnesses; `--bare` needs an API key.
- **Decision:** `claude --restricted -p` with `--tools Bash,Read,Skill`, `--strict-mcp-config`, and a sandbox with `failIfUnavailable`, no unsandboxed commands, and an empty strict allowlist. A live preflight proves auth and that only the candidate skill loads, or stops the run.
- **Alternatives:** `--bare` with an API key. A temporary config dir with copied OAuth credentials. The generic command protocol only.
- **Consequences:** It is unverified whether `--restricted` hides `~/.claude/skills`. If it does not, every Claude run stops by design.

#### Claude live network verification

The live preflight checks direct loopback TCP and the proxy HTTP path against a runner-owned listener.
No live model run has exercised these checks yet. The build uses a stub transport only.
The single preflight call asks Claude to run three exact Bash commands.
One command opens a TCP connection to the listener. Two `curl` commands send an HTTP request to `127.0.0.1` and to `localhost`, with `--noproxy ''` so that the sandbox proxy carries them.
The runner judges the listener. A client that sends the probe token, or resets its connection, fails the preflight with the code `network-isolation-failed`.
A stray client that sends nothing, or sends other bytes, does not count.
A command that differs from the generated command, a missing output, or a malformed output fails it with the same code.
The recorded pass keeps the network result. The reuse key includes the network probe version.
A changed probe version or network setting changes the key. The stage then stops with `environment-differs`, and the user starts a new run.
The native sandbox probe remains a separate, free check.
Domain and proxy policy beyond these probes stays unverified. The preflight does not probe name lookup or a non-loopback address.
The macOS path stays unverified live. A passed preflight does not prove macOS isolation.
The manual macOS checklist is in `skills/skillz/references/experiment-harness.md`.

### ADR-005: Make the wedge arm a hand-written stdlib script  [status: superseded by skillz-pragmatic-autoimprove ADR-003; amended 2026-10-10 for wedge first-class]
- **Context:** The user states that a wedge is a deterministic offload made by hand, and that Python is assumed installed. A real `/wedge` build needs `uv` and network access.
- **Decision:** The `wedge` search mode adds one new `scripts/<name>.py` file plus its SKILL.md call site, seeded from `--brief PATH`. It runs as `python3 -I`. Selection ranks correctness first and tokens second.
- **Alternatives:** A per-evaluation `.pyz` build. Optimizing the brief text only.
- **Consequences:** No build in the loop. Packaging as a `.pyz` stays a separate, optional step.
- **Amendment 2026-10-10 (wedge first-class, C4):** The stdlib-script rule no longer holds for Python helpers. fromargs is the default for an edit to a wedged Python source, and the `prose+cli` reflection prompt says so. The host builds an own target over a site layer. The `@new-cli` component adds a new fromargs target.
  Two cases keep another helper. A non-Python helper keeps its language. A Python helper may be a stdlib `argparse` helper only when the skill has no `wedge.toml` that can build a fromargs target.
  `audit-facts` reports own targets as editable or frozen, and it flags foreign `.pyz` bundles.

### ADR-006: Replace --restricted with project setting sources and a free skill inventory  [status: accepted]
- **Context:** Issue #168. On Claude Code 2.1.289 and later, `--restricted` drops project skills, so the candidate never loads. `disableBundledSkills` keeps Claude Code's own commands and `@builtin` plugins in the init event, and account skills load with the login. Ubuntu's `bwrap-userns-restrict` profile blocks the nested user namespace of the Unix-socket filter, and the documented sysctl does not lift it.
- **Decision:** Run `claude -p --setting-sources project`. Deny Bash writes to the workspace `.claude` directory. Before any model call, send one `initialize` control request, turn off every non-builtin, non-candidate name with `skillOverrides`, and check again. Allow commands with `builtin: true` and plugins with the path `builtin`. When a free probe shows a blocked nested user namespace, set `allowAllUnixSockets: true` and probe a host Unix socket in the live preflight. Raise `preflight-leak` instead of a bare runtime error. Run a free `doctor` before the case draft.
- **Alternatives:** A fixed list of built-in names (breaks on each release). `--bare` (hides the skill list from the model and needs an API key). Unloading the AppArmor profile (needs root and weakens every bwrap user).
- **Consequences:** Account skills cost no extra approval. A skill that `skillOverrides` cannot turn off stops the run before any model call. The Unix-socket fallback widens one sandbox rule on affected hosts; the live socket probe guards it. The live preflight, not `--restricted`, now proves Read-tool confinement. The user approved these choices on 2026-10-08.

### ADR-007: Offer nono as an opt-in isolation backend for the Claude role  [status: accepted]
- **Context:** Issue #169 asks for a backend that hides host setup. A survey found nono the only maintained, no-root tool that wraps any CLI on Linux and macOS with a domain allowlist and credential injection. Research: `research/harness-ergonomics-168-169/harness-ergonomics-168-169.md` in the durable cheese corpus.
- **Decision:** `--isolation nono` runs the whole Claude process in nono on Linux, with Claude Code's sandbox off. The profile grants the workspace and config directory and keeps the staged skills read-only beside the workspace. It allows only `api.anthropic.com`, mediates pathname Unix sockets, and gives the host `ANTHROPIC_API_KEY` only to the nono proxy. Claude and its Bash commands get only a per-session proxy token. The default stays `--isolation claude`.
- **Alternatives:** A runner-owned bwrap and Seatbelt outer sandbox with a Python proxy (more security code to maintain). Docker Sandboxes or microsandbox (need KVM). Anthropic sandbox-runtime (needs Node and has the same seccomp bug).
- **Consequences:** The nono backend bills an API key, not the Claude login. A task command can call `api.anthropic.com` with the proxy token, but cannot read the key or reach another host. macOS stays unverified. A fresh run stops with `host-not-ready`, and a resume stops with `nono-unavailable`. A free local check with a stand-in Claude passed the full preflight under real nono on Ubuntu 26.04. No paid live run has exercised it yet. Under nono, `sandboxed=False` drops the `.claude` deny rule, and the workspace is read-write. A task can create `<workspace>/.claude/skills/*`.

### ADR-008: Score journeys as an autoimprove case kind  [status: accepted]
- **Context:** The skills /culture, /mold, and /cook ask the user questions across several turns. The runner was one-shot, so it could not score them.
- **Decision:** A journey is a case kind that search and the holdout gate score. A simulator role plays the user and answers from a hidden brief. The `journey` grader gates on the trace, then judges the transcript.
- **Alternatives:** A measure-only journey command. A format-only journey with an external runner. No journeys.
- **Consequences:** One live stream-json process runs each journey and keeps `--no-session-persistence`. The simulator never sees `expected` or the rubric. Codex journeys are deferred, and Codex raises `journey-unsupported`. Simulator tokens are not task tokens: `task_usage` holds only the agent usage, and `simulator_usage` records the simulator share, so the skillz-pragmatic-autoimprove token rule never charges them.

### ADR-009: Declare the task tool surface in the contract  [status: accepted]
- **Context:** A journey skill needs more than `Bash,Read,Skill`. It can call MCP servers, agents, and companion skills.
- **Decision:** The contract `surface` block declares `tools`, an MCP config, agents whose frontmatter `name` equals the file stem, and read-only companion skills. The live preflight accepts only the declared names.
  - **Read-only files:** A file that the seed MCP config runs is read-only, because the config starts it on the host; a contract that names it in `editable` or as the helper stops with `contract-surface`. The check matches file names in the seed config text, so it is best-effort. It does not see a file reached through `cwd` and a bare name, or through an import.
  - **Own wedge targets:** An own wedge target whose `.pyz` the seed MCP config names stays frozen and gives search no sources. A `@new-cli` proposal whose `.pyz` the config names is rejected with `contract-surface`.
- **Alternatives:** Keep `Bash,Read,Skill` only. Load the host's real configuration, which breaks ADR-006 and ADR-007. Replace each server with a stub.
- **Consequences:** The surface joins the environment reuse key and the preflight. An MCP server can need sandbox paths that the profile denies. Under `--isolation claude`, MCP stdio servers run as children of Claude Code, outside the Bash sandbox. Under nono, they run inside the nono profile. The MCP config is not editable, because it holds host-run commands. Agents are not editable either, because an edit can add frontmatter keys such as `mcpServers` or `permissionMode`.

### ADR-010: Give journey runs a separate budget  [status: accepted]
- **Context:** A journey with 8 turns costs about 17 calls. A gate of 6 cases with 3 repeats needs about 612 calls.
- **Decision:** A run with a journey case shows a separate journey budget line and asks for approval once. The ceiling is 1000 calls and 28800 seconds. The plan charges 30 seconds for each call, so the seconds cap binds first, at about 960 calls. This amends skillz-pragmatic-autoimprove ADR-005 for those runs.
- **Alternatives:** Journeys only in the gate. A fit inside 200 calls and 7200 seconds with 4 turns and 1 repeat, which weakens ADR-002. Higher global caps.
- **Consequences:** Journey runs take hours. The plan sizes the holdout gate from the sum of its case costs. It sizes each search call at the mean case cost. A mixed manifest then does not pay the journey cost for every case. Anthropic usage terms still limit volume.

### ADR-011: Grow skillz_experiments into pressure-driven slices  [status: accepted]
- **Context:** The package has 28 flat modules. `_workflow.py` has 1262 lines and `_claude.py` has 1595 lines. The borrow list in `.cheese/research/autoimprove-borrow-candidates.md` adds 52 mechanisms, and most of them would land inside `_workflow._Session`.
- **Decision:** One wave with no behaviour change creates four subpackages, each with an `__init__.py` facade: `run/`, `search/`, `gate/`, and `harness/`. `Budget` moves to a shared `_budget.py`. The other flat modules move into `evaluation/`, `cases/`, or `candidate/` when a later wave first touches them. `search/` and `gate/` never import `run/` or `harness/`.
- **Alternatives:** All slices in one wave (largest diff for little gain). A flat split into `_x.py` siblings (no crust). No change (every proposer feature grows `_Session`).
- **Consequences:** The four slices receive 42 of the 52 placed borrow ids. The first wave changes about 41 test import lines. Later waves can run in separate worktrees, but they all wire into `run/_session.py`.

### ADR-012: Run the proposer line first, with the gate and harness waves beside it  [status: accepted]
- **Context:** After the structural wave, seven feature waves remain. The #182 false-promotion ratchet guards gate changes only, and the gate, not the proposer, decides promotion.
- **Decision:** W0 fixes #191, #190, and #193. W1 is the structural wave from ADR-011. The main line is W3 leakage (#174), then W4 proposer (#194 and #153), then W5 search mechanics. W2 gate (#182, #134) and W6 harness and cost (#176, #179) run beside it in separate worktrees. W7 targets (#112) runs last.
- **Alternatives:** A serial order with the gate first. A serial order with the proposer first. The proposer line only, with the other waves as follow-ups.
- **Consequences:** The GEPA and RRSI work lands first. The parallel waves can conflict in `run/_session.py`. W7 waits for a re-ground of #112.

### ADR-013: Treat check_candidate as the O1 cascade first stage  [status: accepted]
- **Context:** OpenEvolve cascade evaluation (O1) needs a cheap deterministic first stage. `check_candidate` already materializes the candidate, checks the SKILL.md name, and runs the helper fixtures. A failure scores 0 before any model call (`_evaluator.py:97`). It runs once per case evaluation, not once per candidate.
- **Decision:** O1 adds no cascade module and no smoke stage. W5 memoizes `check_candidate` by candidate identity within one run, because the contract is frozen for the run. The I1 leakage gate rejects copied case text at proposal time, which is earlier than any cascade stage.
- **Alternatives:** A one-case smoke stage before the GEPA minibatch, which saves calls only when about half of candidates fail it. Deferral until the G3 evaluation policy is measured. No change.
- **Consequences:** O1 closes with a small change. The memo must keep the rejection reason that `check_candidate` sets on the transport. A cross-run cache would need the contract hash in its key.

_Source: Mold session 2026-10-03, Mold sessions 2026-10-10, PR #118 Affinage review, PR #203 merge review, and the issue #168 and #169 research · Updated: 2026-10-11 (ADR-005 amendment, inspector path; ADR-008 to ADR-010; ADR-008 simulator tokens and ADR-009 own wedge targets; ADR-011 to ADR-013 module layout)_
