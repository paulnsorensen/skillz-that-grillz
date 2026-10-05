# skillz autoimprove decisions

These records explain the design of `/skillz autoimprove`, which generalizes the skillz experiment runner at `lib/src/skillz_experiments/`.
The approved spec is `specs/skillz-autoimprove.md` in the durable cheese corpus.
The user settled each decision in a Mold session on 2026-10-03.

## Records

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
- **Consequences:** A helper schema bump regenerates the `HELPER_FIXTURES` outputs in `lib/src/skillz_experiments/_evaluation.py`, `skills/skillz/evals/autoimprove.json`, `lib/src/skillz_experiments/fixtures/self-test.json`, and the `skills/skillz/references/experiments.md` example.

### ADR-004: Run the Claude Code adapter in --restricted mode  [status: accepted]
- **Context:** The Codex adapter isolates discovery, filesystem, and network. Claude Code documents `--restricted` for eval harnesses; `--bare` needs an API key.
- **Decision:** `claude --restricted -p` with `--tools Bash,Read,Skill`, `--strict-mcp-config`, and a sandbox with `failIfUnavailable`, no unsandboxed commands, and an empty strict allowlist. A live preflight proves auth and that only the candidate skill loads, or stops the run.
- **Alternatives:** `--bare` with an API key. A temporary config dir with copied OAuth credentials. The generic command protocol only.
- **Consequences:** It is unverified whether `--restricted` hides `~/.claude/skills`. If it does not, every Claude run stops by design.

#### Claude live network verification

The Claude autoimprove live preflight verifies network denial on the live Linux Bash path.
The single preflight call asks Claude to run a Bash command that connects to a loopback listener that the runner owns.
The runner judges the listener. An accepted connection, a missing output, or a malformed output fails the preflight with the code `network-isolation-failed`.
The recorded pass keeps the network result. The reuse key includes a hash of the network settings and the probe version.
A changed network policy therefore forces a new live preflight.
The native sandbox probe remains a separate, free check.
The macOS path stays unverified live. A passed preflight does not prove macOS isolation.
The manual macOS checklist is in `skills/skillz/references/experiment-harness.md`.
No live provider test validates this boundary in the build.

### ADR-005: Make the wedge arm a hand-written stdlib script  [status: accepted]
- **Context:** The user states that a wedge is a deterministic offload made by hand, and that Python is assumed installed. A real `/wedge` build needs `uv` and network access.
- **Decision:** The `wedge` search mode adds one new `scripts/<name>.py` file plus its SKILL.md call site, seeded from `--brief PATH`. It runs as `python3 -I`. Selection ranks correctness first and tokens second.
- **Alternatives:** A per-evaluation `.pyz` build. Optimizing the brief text only.
- **Consequences:** No build in the loop. Packaging as a `.pyz` stays a separate, optional step.

_Source: Mold session 2026-10-03 and PR #118 Affinage review · Updated: 2026-10-04_
