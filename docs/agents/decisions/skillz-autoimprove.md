# skillz autoimprove decisions

These records explain the design of `/skillz autoimprove`, which generalizes the skillz experiment runner at `lib/src/skillz_experiments/`.
The approved spec is `specs/skillz-autoimprove.md` in the durable cheese corpus.
The user settled each decision in a Mold session on 2026-10-03.

## Records

Skillz-pragmatic-autoimprove ADR-003 removes the `dataset`, `baseline`, `search`, and `evaluate` commands and the `cli`-only and `wedge` arms.
That ADR supersedes the records about those commands and arms in this file. These records are ADR-001 (case manifest location), ADR-002 (arm scoring), and ADR-005 (wedge arm).

### ADR-001: Declare the autoimprove contract in one schema with two locations  [status: accepted]
- **Context:** The runner hard-wires skillz: staging path, `$skillz audit` prompt, `inspect_skill.py` helper, and grader (`_codex.py:145`, `_evaluator.py:24-35`). The [GEPA research](../research/gepa-session-optimization.md) says a generic optimizer accepts a declared build and test contract.
- **Decision:** A contract names the skill, task kinds, invocation, optional helper, grader per kind, and editable files. It lives in `<skill>/evals/autoimprove.json` or in a `target` block of the case manifest; the manifest block wins. With neither, autoimprove asks the user to choose judge-only grading with a powerful model, or to find or draft a contract. A drafted contract carries `"status": "draft"` and `dataset` refuses it until the user approves it.
- **Alternatives:** Inference from SKILL.md and `scripts/` with judge-only grading (no deterministic grade for rewrite skills). A manifest-only or skill-only location.
- **Consequences:** Skills outside this repository work. Each target needs a contract, but autoimprove can draft one.

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
- **Consequences:** A helper schema bump regenerates the `HELPER_FIXTURES` outputs in `lib/src/skillz_experiments/_evaluation.py`, `skills/skillz/evals/autoimprove.json`, and the `skills/skillz/references/experiments.md` example.

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

### ADR-005: Make the wedge arm a hand-written stdlib script  [status: accepted]
- **Context:** The user states that a wedge is a deterministic offload made by hand, and that Python is assumed installed. A real `/wedge` build needs `uv` and network access.
- **Decision:** The `wedge` search mode adds one new `scripts/<name>.py` file plus its SKILL.md call site, seeded from `--brief PATH`. It runs as `python3 -I`. Selection ranks correctness first and tokens second.
- **Alternatives:** A per-evaluation `.pyz` build. Optimizing the brief text only.
- **Consequences:** No build in the loop. Packaging as a `.pyz` stays a separate, optional step.

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

_Source: Mold session 2026-10-03, PR #118 Affinage review, and the issue #168 and #169 research · Updated: 2026-10-08_
