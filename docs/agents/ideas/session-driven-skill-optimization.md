# Session-driven skill prompt and CLI optimization

Session-driven skill prompt and CLI optimization uses a bounded GEPA experiment in skillz.
The editable package contains its prompt, selected references, and an optional helper script.
Approved cases drive evaluation; session frequency is not a success label.
Any skill with an approved contract can be a target; the first target is skillz itself, tested on Codex and Claude Code.
The goal remains “best measured for this use case,” not a globally optimal skill.[^implementation]

## Evidence and scope

The [GEPA research synthesis](../research/gepa-session-optimization.md) separates verified mechanisms from personalization claims.
GEPA's [Universal API](../sources/gepa-optimize-anything.md) supports named prompt and code components.
The implementation pins GEPA 0.1.4 and supplies its own Codex candidate proposer.
It does not invoke either gskill package as a black-box optimizer.[^search]

[PR #105](../sources/skillz-session-analytics-pr-105.md) supplies proposed analytics data, not evaluation labels.
The experiment imports approved normalized cases without depending on that unmerged database implementation.
It does not scan native transcripts.[^cases]

## User workflow

The optional `skillz-experiment` executable exposes six commands.
The skill routes `experiment` to its experiment reference.
Existing `optimize` and `tighten` aliases still mean `improve`.[^implementation]

| Command | Result |
| --- | --- |
| `dataset` | Validated case manifest, frozen seed, and private run directory. |
| `baseline` | Original candidate measurements on train and validation cases. |
| `search --mode prompt` | One GEPA proposal restricted to declared Markdown components. |
| `search --mode prompt-cli` | One GEPA proposal that can also change the inspection helper. |
| `evaluate` | Paired holdout outcomes for three locked arms. |
| `export` | Private local patch and evidence report, without installation. |
| `self-test` | The bounded workflow over public repository-authored cases. |

Run `uv run --project lib --extra experiments skillz-experiment --help` from the repository root.
The skill's `references/experiments.md` documents exact commands and the version-one case schema.
Normal skill installation does not require GEPA.

## From sessions to approved cases

The dataset boundary accepts authored cases or a user-approved normalized analytics export.
Each case supplies an ID, task-family group, split, request, fixture files, expected JSON, provenance, provider approval, and visibility.
Missing request, fixture, oracle, or approval leaves a case diagnostic-only.[^cases]

Keep all cases of one task family in one split.
Paths reject traversal, aliases, hidden components, and runtime-owned collisions.
Private is the default visibility.
Provider approval permits submission, not publication.
Inferred attribution and missing signals remain explicit in provenance.

Analytics cannot reconstruct every replay input.
PR #105 truncates materialized results and omits some native events.
Users must supply reproducible fixtures and independent expected outcomes.
Synthetic cases test the mechanism; they do not demonstrate personalization from real sessions.

## Frozen candidate and evaluator

GEPA optimizes only declared text components.
The seed includes `SKILL.md`, the helper that the contract declares, and explicitly selected Markdown references.
Other package files remain frozen.
The engine, permission policy, dataset, splits, output contract, and evaluator stay outside the candidate.[^candidate]

The skillz inspection helper, declared in its contract, reports lexical frontmatter keys, body line count, and local Markdown link targets.
It does not grade prose or assign fitness.
Independent checks validate helper behavior inside the Codex sandbox.
Task fitness requires the expected JSON result and candidate execution evidence.[^evaluator]

Correctness controls selection.
Measured input-plus-output tokens break correctness ties.
Cached input is already part of input tokens.
The runner records unknown usage as null. Dollar cost remains unknown.

## Codex isolation and native loading

The Codex adapter pins CLI 0.154.0 and requires an explicit model.
The user selects the existing ChatGPT login for the first smoke test.
Temporary isolated home directories keep user configuration and host skills out of candidate discovery.
A temporary authentication symlink references the existing login without engine-side credential copying.[^runtime]

[Codex skill discovery evidence](../sources/codex-skill-discovery-isolation.md) explains why `--ignore-user-config` alone is insufficient.
Host skill discovery and generated-command isolation are different boundaries.
An existing administrator skill directory stops the run.

Candidate commands receive a deny-by-default filesystem profile and no network access.
The candidate package is read-only.
Holdout files, expected answers, the original checkout, and credentials remain outside candidate visibility.
Model-free checks test denied reads, symlink escapes, environment restrictions, and network denial.
A failed isolation check stops execution without an unsafe fallback.

## Budget and sealed holdout

The live run permits at most 20 Codex invocations within 1,200 seconds.
Baseline, search, reflection, failures, and holdout share the same counter and deadline.
Pauses between separate commands consume the same deadline.
The runner reserves six invocations for two holdout cases across three arms.[^workflow]

Selection finishes before holdout evaluation.
The runner locks the candidate hashes of the original, prompt, and prompt-plus-helper (`prompt-cli`) arms.
Holdout feedback never returns to GEPA.
A consumed holdout cannot resume selection or rewrite completed evidence.

The bounded smoke result remains inconclusive for statistical improvement.
Even a higher observed score does not establish robustness or long-term personalization.
An infrastructure failure remains distinct from a task failure.

## Export and remaining work

Exports contain a patch and measurements, not raw requests or expected answers.
Candidates can memorize approved training content.
Therefore, all exports remain private and local until the user completes a separate review.
The runner never applies, installs, merges, or pushes a candidate.[^export]

The adapter is contract-driven.
A skill declares an approved contract at `evals/autoimprove.json`, or in a `target` block of the case manifest.
The shipped example is `skills/skillz/evals/autoimprove.json`.
Without a contract, the runner stops with `contract-missing`.[^contract]
A contract maps each case kind to one of five graders: `exact-json`, `judge`, `command`, `audit`, or `hybrid`.
The skillz inspection helper is now one `exact-json` kind, not the whole evaluator.[^graders]

Codex and Claude Code have built-in transports.
The Claude Code transport runs `claude --restricted`.[^claude]
Another harness needs a trusted command wrapper that implements the command protocol.[^harness]

These items remain future work:

- Built-in transports for harnesses other than Codex and Claude Code.
- Live proof of Claude network isolation. The preflight checks read and write limits, not network denial on live Bash commands.[^isolation]
- Automatic analytics sampling. Approved exports stay manual.
- Repeated-run statistics. No code or reference supports repeated runs.
- Monetary pricing. The runner records tokens only; dollar cost stays unknown.
Session-based personalization needs real approved cases and a task-specific evaluator.
Do not describe the public self-test as a real-session benchmark.

[^implementation]: `lib/src/skillz_experiments/_cli.py:38-127`; `skills/skillz/SKILL.md:33-46`; `skills/skillz/references/experiments.md:1-371`.
[^search]: `lib/src/skillz_experiments/_search.py:29-65`.
[^cases]: `lib/src/skillz_experiments/_cases.py:44-58,128-211`; `_records.py:41-69`.
[^candidate]: `lib/src/skillz_experiments/_candidate.py:52-113`; `_records.py:41-69`.
[^evaluator]: `lib/src/skillz_experiments/_evaluation.py:11-82`; `_evaluator.py:38-71`; `skills/skillz/scripts/inspect_skill.py:85-114`.
[^runtime]: `lib/src/skillz_experiments/_codex.py`; pinned source evidence in [Codex skill discovery and isolation](../sources/codex-skill-discovery-isolation.md).
[^workflow]: `lib/src/skillz_experiments/_workflow.py:101-292,364-416`; `_runtime.py:16-45`.
[^export]: `lib/src/skillz_experiments/_workflow.py:437-471`.
[^contract]: `lib/src/skillz_experiments/_contract.py:34-92,139-175`; `skills/skillz/references/experiments.md:167-200`; `skills/skillz/evals/autoimprove.json`.
[^graders]: `lib/src/skillz_experiments/_graders.py:60-115`; `_evaluator.py:38-130`.
[^claude]: `lib/src/skillz_experiments/_claude.py:259-282`; `_harness.py:19,76-82`.
[^harness]: `lib/src/skillz_experiments/_harness.py:83-86`; `skills/skillz/references/experiment-harness.md:1-30,93-211`.
[^isolation]: `docs/agents/decisions/skillz-autoimprove.md:33-39`; `skills/skillz/references/experiment-harness.md:68-92`.

_Source: user-approved implementation scope, checked repository code, and primary-source research · Updated: 2026-10-05 · Supersedes: proposal-only status and unresolved first-harness choice_
