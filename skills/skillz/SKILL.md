---
name: skillz
description: >
  Add, improve, audit, wedge, or autoimprove a skill. Improve and audit also accept a
  sub-agent definition. The result runs predictably on Claude Code, Codex, OMP, and other Agent Skills hosts.
  Use for /skillz <add|improve|audit|wedge|autoimprove>, "improve this skill",
  "autoimprove this skill",
  "optimize this skill", "tighten this skill",
  "audit this agent", "new skill for X", "skill not triggering", "fix
  trigger rate", or "what in this skill should be a CLI". Do NOT use for
  CLAUDE.md or system-prompt edits, or for code changes that a cheese
  pipeline skill owns.
disable-model-invocation: true
argument-hint: "<add|improve|audit|wedge|autoimprove> [<path>|<name>]"
license: MIT
metadata:
  author: paulnsorensen
  dispatches-agents: audit only, when the host offers sub-agents
---

# skillz

Add, improve, audit, wedge, and autoimprove skills.
Improve and audit also accept agent definitions.
The product is a **predictable** definition: the same process on every run and on every harness.
Every lens asks one question of each line: *does this make the run more predictable, or is it sediment?*

The mode is the first word after the skill name.
The Aliases column lists the names that select the same mode.
Require a target for each mode.
Ask for the mode when it is missing.
Ask for a target when the mode requires one.

## Modes

| Mode | Aliases | Target | Analytics | Writes | Product |
|---|---|---|---|---|---|
| `add <name>` | none | a new skill name | no | creates `skills/<name>/` | a registered skill that passes the rubric |
| `improve <path>` | `optimize`, `tighten` | a `SKILL.md` or agent file | no | edits the target | applied fixes + residual findings |
| `audit <path>` | none | a `SKILL.md` or agent file | yes | the analytics cache only; never the target | calibrated report |
| `wedge <path>` | none | a `SKILL.md` | no | none | a `/wedge` handoff brief per offload candidate |
| `autoimprove <path>` | `experiment` | a skill with an autoimprove contract, or the public self-test | approved normalized cases only | isolated run directory | paired measurements and a private candidate patch |

`audit` never modifies the target. Its Usage ceremony runs `ingest.py`, which creates or refreshes the analytics cache.
`references/analytics-ceremony.md` has the details. Skip Usage and the raw-log fallback write no cache.

For `autoimprove`, read `references/experiments.md`. Follow its workflow, not the shared audit protocol.
When the user selects a harness command or the `claude` adapter, also read `references/experiment-harness.md`.
Ask which harness command and model the user wants before setup.
When the target has no contract, follow `No contract` in `references/experiments.md`.
Use the installed `scripts/skillz-experiment.pyz`. Do not require a source checkout.

## Shared protocol

### 1. Read and classify

Read the target. Read a linked reference only when its stated trigger matches the run.
A full-package audit may explicitly read every file in the target package.
For `audit` of a skill directory, run `python3 <this-skill-directory>/scripts/skillz-experiment.pyz audit-facts <skill-directory>` before applying the rubric.
Also run `python3 <this-skill-directory>/scripts/inspect_skill.py <target>` for the prose facts.
Use both JSON outputs as objective package facts, not a fitness score. An agent file has no package, so skip `audit-facts`.
Read `references/experiments.md § Audit facts contract` for the check ids and statuses.
Report a helper error without treating it as a successful audit.
Classify it as **agent** (`tools:` / `disallowedTools:` or an `agents/registry.yaml` entry) or **skill** (`name:` + `description:`).
Report the body size as `~N tok` against the 5k budget.
For a skill, copy it from the `body.token-estimate` check. For an agent file, count bytes/4 after the frontmatter block.
List the deploy targets the definition reaches (`references/harness-layout.md § Layout`).

### 2. Rubric

Score each lens.
Each row names the principle, the failure mode it catches, and a checkable test.
Read `references/anti-patterns.md` when a finding needs the expanded form.

| Lens | Principle → catches | Check |
|---|---|---|
| **Predictability** | Fixed protocol with checkable completion → «premature completion» | Every step ends on a done-condition an agent can verify. |
| **Invocation** | `description` = trigger conditions, front-loaded, third person → «workflow summary», «summary description» | Trigger phrases + "Do NOT use for"; no internal workflow; length comes from `description.length`; first sentence carries the trigger. `references/description-optimization.md`. |
| **Portability** | Spec-core frontmatter plus additive Claude fields; user-only policy on every host → «Claude-only assumption», «sidecar missing», «argument-variable dependence» | Cite failed `audit-facts` checks for keys, sidecar pairing, and argument, skill-directory, or file-mention syntax. Judge by hand: args are parsed from the text after the skill name; skills are cross-referenced by `/name`; dispatch and GitHub ops name the contract before host syntax. Full matrix: `references/harness-layout.md`. |
| **Information hierarchy** | Disclose only what some runs skip; body ≤5k tok; references one level deep, each with a read trigger → «sprawl», «untriggered split», «file-mention force-load» | Cite failed `audit-facts` checks for the token budget, nested references, orphans, and missing read triggers. Judge by hand: relocation counts only when runs branch on the block and the `## References` entry names the trigger. `references/progressive-disclosure.md`. |
| **Prose (ASD-STE100)** | Active voice, present tense, one instruction per sentence, short sentences → «passive voice», «multi-instruction sentence», «long sentence» | Cite `inspect_skill.py` `long_sentences` facts (sentences over 25 words). Cite `advisory_sentences` (21 to 25 words) only for procedural steps. Report passive voice and multi-instruction sentences as findings. |
| **Leading words** | One pretrained word beats a restated triad → «duplication», «no-op weak word» | Collapse restatements; strengthen weak words (`be thorough` → `relentless`). |
| **Pruning** | Single source of truth; delete no-ops → «sediment» | No meaning in two places; no line the model obeys by default. Delete whole sentences. |
| **Deterministic offload** | Fixed computation runs as a bundled command, not regenerated prose → «inline script» | No step makes the model write or re-derive the same parse, count, filter, sort, or projection on every run; `scripts.invocation-line` covers the invocation line; judge whether each bundled script has an output contract. Fix through `wedge`. |
| **Tool scoping** | Read-only / write-scoped / focused; use host enforcement when available → «prose-only constraint» | Claude skills use `disallowed-tools` to remove tools for the current turn. Their `allowed-tools` grants permission without prompts; it is not a deny list. Claude agents use `disallowedTools`. Report actual enforcement per mode and mark prose-only limits as degraded. Do not disable writes for `improve`. |
| **Context & fork** | Fork when output > ~500 lines or only a digest is needed → «monolithic output» | Fork matches size; a wrap-up signal exists; the `model-policy.*` checks cover `model:` + `effort:`. |
| **Prompt quality** | Positive framing, why-over-what, one strong example, "What this never does" → «negation-heavy», «rules without reasons» | Judgment tasks use a scaffold, not always/never. `references/decision-frameworks.md`. |
| **Calibration** | Judgment agents tag confidence × severity → «judgment without calibration» | `<certain>` / `<speculative>` / `<don't know>`; don't-know never surfaces. |
| **Output format** | Summary first, tables for findings, clean-vs-issues signal → «no output format» | Format defined; summary and detail split. |
| **Usage** *(audit)* | Declared matches actual → «declared-vs-actual», «decay» | Declared tools are the used tools; error rate near baseline; usage not declining. |

### 3. Calibrate

Tag each finding with severity × confidence.
The kernel is `references/calibration.md`; the defaults:

- **Severity by lens** — Predictability, Invocation, Portability, Tool scoping, Calibration → `high`; Information hierarchy, Context & fork, Pruning → `high`/`medium`; Leading words, Prose, Deterministic offload, Output format, Usage → `medium`.
- **Confidence** — a cited line plus a concrete failure, a reference that does it right, or analytics data → `<certain>`; a checkable but unverified observation → `<speculative>`; a misread → `<don't know>`, dropped.
- **Re-derive** — re-read the target and re-derive each `<speculative>` finding once without the first pass. Drop it when it does not reproduce.
- Order by severity, then `<certain>` first.

## Prose check

`add` step 5 and `improve` step 5 run this check.

1. Run `python3 <this-skill-directory>/scripts/inspect_skill.py <target file>` on the `SKILL.md` or agent file.
2. Rewrite every sentence in `long_sentences` (over 25 words).
   Rewrite each `advisory_sentences` entry (21 to 25 words) that is a procedural step.
3. For a changed reference file, apply both limits by hand: 25 words, or 20 words for a procedural step.

The helper rejects a file that has no frontmatter, so it cannot check a reference file.

## Mode: add

1. Confirm the name: kebab-case, ≤64 chars, directory name equals `name:`, and no collision in `skills/`, `~/.claude/skills`, `~/.agents/skills`.
2. Collect from the argument, or ask for: a one-sentence purpose, ≥5 trigger phrases, anti-triggers, invocation policy, and the harness set.
   The invocation policy is model-invoked or user-only.
3. Write the skill from `references/harness-layout.md § Template`.
   Keep the body ≤2k tok at birth.
   Create a reference only for a block that some runs skip.
   Add `agents/openai.yaml` when the skill is user-only.
   Set `model` + `effort` only when the skill is model-invoked.
4. Register the skill in its repo's index (`references/harness-layout.md § Registration`).
   A repo-local skill stays out of every global list; place it per `references/harness-layout.md § Repo-local skills`.
5. Run the prose check on `SKILL.md` and on each changed reference before you report the mode as done.
6. Run `improve` on the new file once.
   Then run the repo's deploy step when one exists.
   Example deploy steps are a dotfiles sync, `npx skills add`, or a copy into the harness skills directory.

Done means: the file exists and the repo index names it, or every repo-local host path resolves.
The deploy step exits 0 when one exists. The Invocation lens passes.
The inspector reports an empty `long_sentences` list.

## Mode: improve

1. Run the shared protocol without analytics.
2. Apply every `<certain>` finding of severity medium or higher that does not change protocol semantics.
   Record each Deterministic offload finding as a residual for `/skillz wedge`; a new CLI is a redesign.
3. Put every `<speculative>` finding and every protocol-semantic change to the user as one approval question, with your recommendation for each.
   Apply the approved ones and record the declined ones.
   A delegated run returns these findings to its parent, and the parent asks.
   A PR body or a report is not approval.
4. Keep the target's voice and protocol semantics. Tighten; do not redesign.
5. Run the prose check on the target file and on each changed reference before you report the mode as done.
6. Re-measure the body. Report before/after tokens and the residual findings.
7. Run the repo's deploy step when the target lives under a `skills/` or `agents/` tree that a sync distributes.
   Confirm the deployed copy matches the source; a vendored skill with the same name overwrites a local one (`harness-layout.md § Layout`).

Done means: every `<certain>` finding above `low` is fixed or recorded as an explicitly accepted residual.
The user approves or declines every item submitted for approval.
The body is ≤5k tok. The inspector reports an empty `long_sentences` list. The repo's quality gate exits 0.

## Mode: audit

1. Run shared protocol §1 Read and classify to resolve `target_kind`.
2. Run `references/analytics-ceremony.md` with that kind. If DuckDB is absent, read `references/raw-log-fallback.md` instead. Its step 1 asks Skip Usage or scan; never run the scan without consent.
3. Run shared protocol §2-3 with the Usage digests.
4. Emit the report below. Do not modify the target. Write nothing else except the analytics cache.
5. Close with `Run /skillz improve <path> to apply.`

Done means: the report lists every surfaced finding with a cited line, and the below-bar count is stated.

## Mode: wedge

Find the work in the target that belongs in a bundled CLI, then write the brief that `/wedge` builds from.
The user runs `/wedge`; this mode writes no code and starts no build, because packaging is `/wedge`'s contract.

1. Read the target and its `scripts/`. Score the Deterministic offload lens only.
   For an agent file, report that `/wedge` packages only skills, then stop.
2. List each candidate with its line.
   A candidate is a fixed parse, validation, count, filter, sort, or projection that every run repeats.
   A bundled script without an output contract is also a candidate.
   Classification, recommendations, and user decisions stay in prose; they are never candidates.
3. Write each candidate's behavior contract: command name, inputs, output shape, ordering with tie-breaks, empty result, errors, and side effects.
   Name the prose that stays and the target line that will call the command.
4. Rank candidates by how often a run repeats the work, then by target line, ascending. With zero candidates, report `No offload candidates` and stop.
5. Emit the brief below. Close with `Run /wedge with candidate <n> of this brief.`
   When `/wedge` is not installed, add its install command: `npx skills add paulnsorensen/skillz-that-grillz --skill wedge`.

Done means: every candidate cites a line and has all seven contract fields.

```markdown
## skillz wedge: <name>

| # | Step (line) | Transformation | Command | Stays in prose |
|---|---|---|---|---|

### Candidate <n>: <command>
**Inputs** · **Output** · **Ordering** · **Empty** · **Errors** · **Side effects** · **Caller line**
```

## Report

```markdown
## skillz <mode>: <name>

- Type: agent | skill · Invocation: model | user-only · Tools: <N allowed, N disallowed> · Body: ~N tok (before → after for improve)
- Harnesses reached: <list> · Findings: N surfaced, N below the bar

| # | Severity | Confidence | Lens | Issue (line) | Fix | Applied (yes / approved / declined) |
|---|---|---|---|---|---|---|

### Detail (per surfaced finding)
**What** · **Why** (the principle) · **How** (exact section) · **Reference** (a definition that does it right)

### Recommended hooks
Only for a rule that must hold every time; pick from `references/hooks-catalog.md`. Omit when none.

### Below the bar
N findings were `<don't know>` or trivial (not shown).
```

## What this skill never does

- `audit` never modifies the target and writes only the analytics cache; `wedge` never writes; `improve` never redesigns; `add` never registers a skill whose description fails the Invocation lens.
- It never surfaces `<don't know>`.
- It never exempts itself; a finding against `skillz` is filed like any other.

## Gotchas

- Generic findings on simple skills: every finding cites a line or a named pattern.
- Over-indexing on missing `disallowedTools` when the host default already constrains the tool list.
- The `<speculative>` re-derivation is the first step skipped under time pressure and the one that catches the most false positives.
- `disable-model-invocation` alone leaves Codex free to auto-invoke; the sidecar is the fix, not more prose.
- A hook is for a rule that must hold every time, not for every finding.

## References

Read on demand:

- `references/harness-layout.md` — Portability lens fires or `add` (global or repo-local); the frontmatter matrix, rules, template, sidecar, and sources.
- `references/analytics-ceremony.md` — `audit`; use the bundled engine for best-effort per-pack analytics.
- `references/raw-log-fallback.md` — DuckDB is absent and the user opts into a sampled, read-only Usage scan.
- `references/anti-patterns.md` — a finding needs the expanded failure mode.
- `references/progressive-disclosure.md` — Information hierarchy fires.
- `references/description-optimization.md` — Invocation fires.
- `references/decision-frameworks.md` — Prompt quality flags rigid rules on a judgment task.
- `references/hooks-catalog.md` — a finding needs 100%-of-the-time enforcement, or Invocation recommends a forced-evaluation hook (§1).
- `references/skill-usage.md`, `references/agent-orchestration.md`, `references/drift-regression.md` — the analytics packs. For `audit`, pass each pack path and the target kind to its pack context. Read a pack only when the host has no sub-agents.
- `engine/scripts/` and `engine/references/` — internal analytics ingestion, query, schema, conventions, and coverage; not a separate skill.
- `references/calibration.md` — the confidence × severity kernel.
- `references/experiments.md` — `autoimprove`, or an `audit` that needs a check id; the contract, graders, wedge mode, case schema, isolation, the frozen inspection contract, and the audit facts contract.
- `references/experiment-harness.md` — the user selects a command or `claude` adapter, or separate roles.
