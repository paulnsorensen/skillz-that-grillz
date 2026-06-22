# The canonical agent gate: `build` + `ci`

Every justfile this skill generates exposes **one** command an agent runs after
every change — `just build` — and its no-autofix twin `just ci`. This is the
default recipe. Everything else (run, dev, dist, docs, clean) is secondary.

## Why one command, compacted

Three findings drive this design. They are evidence for the *shape*, not
recommendations to copy verbatim — the recipes below are the implementation.

| Claim | Evidence | Confidence |
|-------|----------|------------|
| A consistent, purpose-built command interface lifts agent success far more than a bigger model does | Yang et al., *SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering*, arXiv:2405.15793 (NeurIPS 2024) — a stable ACI gave a **64% relative** increase in SWE-bench resolution with the same LM | `certain` |
| Deterministic, verifiable feedback (tests/execution) beats fuzzy or absent feedback | Ridnik et al., *Code Generation with AlphaCodium*, arXiv:2401.08500 — test-driven iteration lifted GPT-4 CodeContests pass@5 from **19% → 44%**. Reinforced by Chen et al., *Teaching LLMs to Self-Debug*, arXiv:2304.05128 | `certain` |
| Compact, actionable output (errors + file:line, not verbose success logs) keeps the feedback loop in-budget | Anthropic, *Writing effective tools for AI agents* (2025) — concise vs detailed tool output measured at 72 vs 206 tokens; explicit guidance to return actionable errors. Anthropic, *Best practices for Claude Code* — "give the model a way to verify its work… the single highest-leverage thing you can do" | `certain` for the guidance; a peer-reviewed A/B isolating *error-only formatting → task-success delta* is `don't know` |

The throughline: agents converge faster when they (1) always run the same
command, (2) get a deterministic pass/fail, and (3) aren't drowned in
success-case noise. `just build` is that command.

## The recipes

`build` autofixes then verifies; `ci` runs the identical verification with no
autofix, so a clean `ci` locally means a clean CI. Both compact their output:
one `✓ <step>` line per passing step, and the full tool output (with file:line)
only when a step fails — at which point the gate aborts immediately.

```just
# The one command to run after every change.
default: build

# Canonical gate — autofix, then lint, typecheck, test, coverage. Compact output.
build: (_gate "fix")

# CI gate — identical checks, NO autofix. A clean run here == a clean CI.
ci: (_gate "check")

[private]
[no-exit-message]
[script("bash")]
_gate mode:
    set -uo pipefail
    # Run a step quietly: print "✓ name" on success; on failure print the full
    # captured output (tools emit file:line) and abort the whole gate.
    step() { local n=$1; shift; local o
        if o=$("$@" 2>&1); then echo "✓ $n"
        else echo "✗ $n"; printf '%s\n' "$o"; exit 1; fi; }
    # language-specific steps go here, branching on {{mode}} for fix vs check
```

`step` buffers each command and reveals nothing until it finishes — the same
quiet-on-success, dump-on-failure trick as
[`chronic`](https://joeyh.name/code/moreutils/) (moreutils), inlined here so
each step gets its own named `✓`/`✗` label, which `chronic` alone wouldn't give.
The trade-off that buys the compaction: a slow suite shows nothing until it's
done. The agent gets a clean `✓`/`✗` ledger instead of scrolling banners.

`[no-exit-message]` on `_gate` suppresses just's own
`error: recipe '_gate' failed…` line, which would otherwise trail the clean
ledger on every failure. The gate still exits non-zero — the attribute only
drops the redundant banner, keeping the failure output to the `✗` step plus its
captured `file:line`.

### rtk upgrade path

The `step` helper is portable (bash only). When the project has `rtk` installed,
swap `step test CMD` for `rtk test CMD` (and `step <other> CMD` for `rtk err
CMD`) to get per-tool smart filtering on top of quiet-on-success — see
`rtk.md`. Don't require rtk; the bash `step` is the floor.

### No `[script]` / older just

On a just older than 1.44 (where `[script]` isn't yet stable), or if you'd
rather avoid the `[script]` attribute, make `_gate` a shebang recipe instead — the
parameterized dependency (`build: (_gate "fix")`) works regardless of body type:

```just
[no-exit-message]
_gate mode:
    #!/usr/bin/env bash
    set -uo pipefail
    step() { local n=$1; shift; local o
        if o=$("$@" 2>&1); then echo "✓ $n"
        else echo "✗ $n"; printf '%s\n' "$o"; exit 1; fi; }
    ...
```

## Mandatory doc updates

Generating the recipes is half the job. The point of one canonical command is
lost if the agent context file doesn't tell agents to use it. After writing the
justfile, **always** update both:

### 1. Agent context file (`AGENTS.md` / `CLAUDE.md` / `.cursor/rules`)

Add or replace a build section with this (adapt the command list to the
ecosystem, keep the "ALWAYS run it" framing):

```markdown
## Build & verification

This project has ONE canonical verification command. ALWAYS run it after
changing code, and treat a non-zero exit as a hard stop — fix the reported
failures before doing anything else.

- `just build` — autofix (format + lint --fix), then lint, typecheck, test,
  and coverage. Run this after every change.
- `just ci` — the same gate with no autofixes; this is exactly what CI runs.

Output is compacted: each step prints `✓ <step>` on success and the full tool
output (with file:line) only on failure. Do not invent ad-hoc test/lint
commands — run `just build` so your feedback loop matches CI.
```

If the repo has no agent context file yet, create `AGENTS.md` with this section.

### 2. CI workflow

Point the existing CI verification step at `just ci` so local and CI run the
identical gate. This skill does **not** design pipelines — it only redirects the
verify step. In a GitHub Actions job that already checks out and sets up the
toolchain, the verification step becomes:

```yaml
      - uses: extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3 # v4
      - run: just ci
```

**Install `just` from `setup-just`, never distro `apt`.** The `_gate` uses the
`[script]` attribute, stable since just 1.44; Ubuntu/Debian apt ship a much
older just (~1.16) that fails with an unknown-attribute error. Use the
`setup-just` action (or a pinned release binary), pinned by commit SHA.

**Install every tool the gate shells out to, pinned.** `just ci` is only as
green as the runner's `PATH`. Each `step <name> <cmd>` invokes a real binary
(`yamlfmt`, `markdownlint-cli2`, the test runner…); a tool the runner lacks
fails the gate with `command not found`, not a real defect. Add an install step
that pins the same versions a developer runs locally — including formatters that
have no apt/npm/pip package (`yamlfmt` ships only as a release binary).

Collapse any separate `lint` / `test` / `coverage` steps into the single `just
ci` call. If multiple jobs each ran a slice of the checks, they now all run
`just ci` (or are merged into one job) — one gate, one definition.
