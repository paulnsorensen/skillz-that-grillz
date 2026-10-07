---
name: skillz
description: >
  Add, improve, audit, wedge, or autoimprove a skill. Improve and audit also accept a
  sub-agent definition. The result runs predictably on Claude Code, Codex, OMP, and other Agent Skills hosts.
  Use for /skillz <add|improve|audit|wedge|autoimprove>, "improve this skill",
  "autoimprove this skill",
  "optimize this skill", "tighten this skill",
  "audit this agent", "new skill for X", "skill not triggering", "fix
  trigger rate", "wedge this", "turn this repeated work into a CLI", or
  "what in this skill should be a CLI". Do NOT use for
  CLAUDE.md or system-prompt edits, or for code changes that a cheese
  pipeline skill owns.
disable-model-invocation: true
argument-hint: "<add|improve|audit|wedge|autoimprove> [<path>|<name>]"
license: MIT
metadata:
  author: paulnsorensen
  dispatches-agents: audit and improve, when the host offers sub-agents
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
| `improve <path>` | `optimize`, `tighten` | a `SKILL.md` or agent file | yes, unless it reuses a current audit | the analytics cache; the target after approval | approved fixes + residual findings |
| `audit <path>` | none | a `SKILL.md` or agent file | yes | the analytics cache only; never the target | calibrated report |
| `wedge <path>` | none | a `SKILL.md` | no | the target skill: each selected CLI, its delivery files, its call site, and any dependency write | an offload brief, then one CLI per selected candidate, each in its own directory when wedged, or with a stated reason |
| `autoimprove <path>` | `experiment` | a skill directory; no contract or manifest | cases that the user approves; agent-drafted session cases when analytics exists | isolated run directory | a holdout gate verdict and a private candidate patch |

`audit` never modifies the target. The Usage ceremony of `audit` and `improve` runs `ingest.py`, which creates or refreshes the analytics cache.
`references/analytics-ceremony.md` has the details. Skip Usage and the raw-log fallback write no cache.

For `autoimprove`, read `references/experiments.md`. Follow its workflow, not the shared audit protocol.
The agent drafts the cases. The user needs no contract and no case manifest.
Run `scripts/skillz-experiment.pyz run`, answer each coded stop, and then run `export`.
Ask at most three questions: harness and model, case approval, and budget approval.
When the user selects the `claude` adapter or a custom command check, also read `references/experiment-harness.md`.
Use the installed `scripts/skillz-experiment.pyz`. Do not require a source checkout.

## Shared protocol

### 1. Read and classify

Read the target. Read a linked reference only when its stated trigger matches the run.
A full-package audit may explicitly read every file in the target package.
For a skill directory, run `python3 <this-skill-directory>/scripts/skillz-experiment.pyz audit-facts <skill-directory>` before applying the rubric.
Also run `python3 <this-skill-directory>/scripts/inspect_skill.py <target>` for the prose facts.
Use both JSON outputs as objective package facts, not a fitness score. An agent file has no package, so skip `audit-facts`.
Read `references/experiments.md § Audit facts contract` for the check ids and statuses.
Report a helper error without treating it as a successful audit.
Classify it as **agent** (`tools:` / `disallowedTools:` or an `agents/registry.yaml` entry) or **skill** (`name:` + `description:`).
For a skill, run `git ls-files -z -co --exclude-standard <skill-dir> | LC_ALL=C sort -z | xargs -0 git hash-object | git hash-object --stdin`.
Its output is the content id. It covers every package file, so a sidecar or reference edit changes it.
For an agent file, run `git hash-object <target>`. Its output is the content id.
When a command fails, the content id is `unavailable`.
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
| **Invocation** | `description` = trigger conditions, front-loaded, third person → «workflow summary», «summary description» | Trigger phrases + "Do NOT use for"; no internal workflow; skills take length from `description.length`, agent files stay at ≤1024 chars by hand; first sentence carries the trigger. `references/description-optimization.md`. |
| **Portability** | Spec-core frontmatter plus additive Claude fields; user-only policy on every host → «Claude-only assumption», «sidecar missing», «`$ARGUMENTS` dependence» | Cite failed `audit-facts` checks for keys, sidecar pairing, and argument, skill-directory, or file-mention syntax. Judge by hand: args are parsed from the text after the skill name; skills are cross-referenced by `/name`; dispatch and GitHub ops name the contract before host syntax. Full matrix: `references/harness-layout.md`. |
| **Information hierarchy** | Disclose only what some runs skip; body ≤5k tok; references one level deep, each with a read trigger → «sprawl», «untriggered split», «`@file` force-load» | Cite failed `audit-facts` checks for the token budget, nested references, orphans, and missing read triggers. Judge by hand: relocation counts only when runs branch on the block and the `## References` entry names the trigger. `references/progressive-disclosure.md`. |
| **Prose (ASD-STE100)** | Active voice, present tense, one instruction per sentence, short sentences → «passive voice», «multi-instruction sentence», «long sentence» | Cite `inspect_skill.py` `long_sentences` facts (sentences over 25 words). Cite `advisory_sentences` (21 to 25 words) only for procedural steps. Report passive voice and multi-instruction sentences as findings. |
| **Leading words** | One pretrained word beats a restated triad → «duplication», «no-op weak word» | Collapse restatements; strengthen weak words (`be thorough` → `relentless`). |
| **Pruning** | Single source of truth; delete no-ops → «sediment» | No meaning in two places; no line the model obeys by default. Delete whole sentences. |
| **Deterministic offload** | Fixed computation runs as a bundled command, not regenerated prose → «inline script» | No step makes the model write or re-derive the same parse, count, filter, sort, or projection on every run; `scripts.invocation-line` covers the invocation line; judge whether each bundled script has an output contract. Fix with `references/offload.md`. |
| **Tool scoping** | Read-only / write-scoped / focused; use host enforcement when available → «prose-only constraint» | Claude skills use `disallowed-tools` to remove tools for the current turn. Their `allowed-tools` grants permission without prompts; it is not a deny list. Claude agents use `disallowedTools`. Report actual enforcement per mode and mark prose-only limits as degraded. Do not disable writes for `improve`. |
| **Context & fork** | Fork when output > ~500 lines or only a digest is needed → «monolithic output» | Fork matches size; a wrap-up signal exists; the `model-policy.*` checks cover `model:` + `effort:`. |
| **Prompt quality** | Positive framing, why-over-what, one strong example, "What this never does" → «negation-heavy», «rules without reasons» | Judgment tasks use a scaffold, not always/never. `references/decision-frameworks.md`. |
| **Calibration** | Judgment agents tag confidence × severity → «judgment without calibration» | `<certain>` / `<speculative>` / `<don't know>`; don't-know never surfaces. |
| **Output format** | Summary first, tables for findings, clean-vs-issues signal → «no output format» | Format defined; summary and detail split. |
| **Usage** *(audit, improve)* | Declared matches actual → «declared-vs-actual», «decay» | Declared tools are the used tools; error rate near baseline; usage not declining. |

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
6. Run `improve` on the new file once. Skip Usage, because a new skill has no sessions.
   Then run the repo's deploy step when one exists.
   Example deploy steps are a dotfiles sync, `npx skills add`, or a copy into the harness skills directory.

Done means: the file exists and the repo index names it, or every repo-local host path resolves.
The deploy step exits 0 when one exists. The Invocation lens passes.
The inspector reports an empty `long_sentences` list.

## Mode: improve

1. Get a current audit of the target.
   Reuse an audit report from this conversation when its target and content id match. Name the reused report.
   An `unavailable` content id never matches.
   Otherwise run `audit` steps 1-3. When Usage is absent, state why.
2. Show the findings table before the first edit, with one recommendation per finding.
   Recommend `apply` for a `<certain>` finding of severity medium or higher that does not change protocol semantics.
   Recommend `offload` or `decline` for a Deterministic offload finding. An offload changes protocol semantics, so it needs explicit approval.
   Recommend `apply` or `decline` for every other finding. Give the reason.
3. Ask the user one approval question that covers every surfaced finding.
   Apply the approved findings. Implement each approved offload with `references/offload.md`.
   Record the declined findings as residuals.
   A delegated run returns the findings to its parent, and the parent asks.
   A PR body or a report is not approval.
4. Keep the target's voice and protocol semantics. Change them only for an approved item.
5. Run the prose check on the target file and on each changed reference before you report the mode as done.
6. Re-measure the body. Report before/after tokens and the residual findings.
7. Run the repo's deploy step when the target lives under a `skills/` or `agents/` tree that a sync distributes.
   Confirm the deployed copy matches the source; a vendored skill with the same name overwrites a local one (`harness-layout.md § Layout`).

Done means: the report names the reused audit or states that the audit is fresh.
The user sees every surfaced finding before the first edit. The user approves or declines each finding that the approval question covers.
The run applies every approved finding.
The body is ≤5k tok. The inspector reports an empty `long_sentences` list. The repo's quality gate exits 0.

## Mode: audit

1. Run shared protocol §1 Read and classify to resolve `target_kind`.
2. Run `references/analytics-ceremony.md` with that kind. If DuckDB is absent, read `references/raw-log-fallback.md` instead. Its step 1 asks Skip Usage or scan; never run the scan without consent.
3. Run shared protocol §2-3 with the Usage digests.
4. Emit the report below. Do not modify the target. Write nothing else except the analytics cache.
5. Close with `Run /skillz improve <path> to apply.` In this conversation, `improve` reuses this report while the content id matches.

Done means: the report lists every surfaced finding with a cited line, and the below-bar count is stated.

## Mode: wedge

Find the fixed work in the target skill and move it into CLIs.
Follow `references/offload.md`; its steps are Find, Contract, Implement, Deliver, and Wire.

1. Read the target and its `scripts/`. Score the Deterministic offload lens only.
   For an agent file, report that `wedge` changes only skills, then stop.
2. Run Find and Contract for each candidate. Cite each candidate's line.
   Rank candidates by how often a run repeats the work, then by target line, ascending.
   With zero candidates, report `No offload candidates` and stop.
3. Emit the brief below.
   Ask the user which candidates to implement, with your recommendation.
   Ask in the same question whether the user declines fromargs or wedging.
   Name each dependency write in that question.
4. Run Implement, Deliver, and Wire for each selected candidate.
   Read `references/fromargs.md` before Implement, unless the user declined fromargs.
   Read `references/wedge-packaging.md` before you wedge a CLI.
5. Run the prose check on the target `SKILL.md`. Then run the repo's quality gate.
6. Run the repo's deploy step when a sync distributes the target, as improve step 7 does.
   Confirm that the deployed copy matches the source.

Done means: every candidate cites a line and has all seven contract fields.
Each selected CLI passes its contract tests through its invocation line.
Each CLI that is not wedged states the reason.

```markdown
## skillz wedge: <name>

| # | Step (line) | Transformation | Command | Delivery | Stays in prose |
|---|---|---|---|---|---|

### Candidate <n>: <command>
**Inputs** · **Output** · **Ordering** · **Empty** · **Errors** · **Side effects** · **Caller line**
```

## Report

```markdown
## skillz <mode>: <name>

- Type: agent | skill · Invocation: model | user-only · Tools: <N allowed, N disallowed> · Body: ~N tok (before → after for improve)
- Harnesses reached: <list> · Findings: N surfaced, N below the bar
- Target: <path> · Content id: <id | unavailable> · Usage: included | omitted (<reason>) · Audit: fresh | reused <content id> (improve only)

| # | Severity | Confidence | Lens | Issue (line) | Fix | Recommendation (apply / decline / `/skillz wedge`) | Decision (approved / declined / pending / residual; improve only) |
|---|---|---|---|---|---|---|---|

### Detail (per surfaced finding)
**What** · **Why** (the principle) · **How** (exact section) · **Reference** (a definition that does it right)

### Recommended hooks
Only for a rule that must hold every time; pick from `references/hooks-catalog.md`. Omit when none.

### Below the bar
N findings were `<don't know>` or trivial (not shown).
```

## What this skill never does

- `audit` never modifies the target and writes only the analytics cache.
- `wedge` writes only the candidates the user selects.
- `improve` never edits before the user answers its approval question, and it never changes protocol semantics without approval.
- `add` never registers a skill whose description fails the Invocation lens.
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
- `references/analytics-ceremony.md` — `audit`, or `improve` without a reusable audit; use the bundled engine for best-effort per-pack analytics.
- `references/raw-log-fallback.md` — DuckDB is absent and the user opts into a sampled, read-only Usage scan.
- `references/anti-patterns.md` — a finding needs the expanded failure mode.
- `references/offload.md` — when `wedge` runs, or when `improve` implements an approved offload; the shared procedure.
- `references/fromargs.md` — when an offload writes a fromargs CLI.
- `references/wedge-packaging.md` — before an offload wedges a CLI; the builder is `wedge/scripts/wedge.pyz`.
- `assets/records.py`, `assets/wedge.toml` — the CLI and manifest templates that an offload copies.
- `evals/evals.json` — pressure scenarios; use them when you change the offload procedure.
- `references/progressive-disclosure.md` — Information hierarchy fires.
- `references/description-optimization.md` — Invocation fires.
- `references/decision-frameworks.md` — Prompt quality flags rigid rules on a judgment task.
- `references/hooks-catalog.md` — a finding needs 100%-of-the-time enforcement, or Invocation recommends a forced-evaluation hook (§1).
- `references/skill-usage.md`, `references/agent-orchestration.md`, `references/drift-regression.md` — the analytics packs. For the analytics ceremony, pass each pack path and the target kind to its pack context. Read a pack only when the host has no sub-agents.
- `engine/scripts/` and `engine/references/` — internal analytics ingestion, query, schema, conventions, and coverage; not a separate skill.
- `references/calibration.md` — when you tag a finding's severity and confidence; the confidence × severity kernel.
- `references/experiments.md` — `autoimprove`, or an `audit` that needs a check id; the run flow, case draft, gate verdict, export, optional contract, graders, isolation, and the helper contracts.
- `references/experiment-harness.md` — the user selects the `claude` adapter, or checks a custom command or role files.
