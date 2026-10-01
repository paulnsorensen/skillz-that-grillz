# Session-driven skill prompt and CLI optimization

Session-driven skill prompt and CLI optimization uses a bounded GEPA experiment in skillz.
The editable package contains its prompt, selected references, and inspection helper.
Approved cases drive evaluation; session frequency is not a success label.
The first adapter targets skillz audit tasks on Codex, not arbitrary skill-owned programs.
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
The seed includes `SKILL.md`, the skill-owned inspection helper, and explicitly selected Markdown references.
Other package files remain frozen.
The engine, permission policy, dataset, splits, output contract, and evaluator stay outside the candidate.[^candidate]

The inspection helper reports lexical frontmatter keys, body line count, and local Markdown link targets.
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

The current adapter evaluates the frozen inspection-helper contract.
Generic CLI contracts, additional harnesses, automatic analytics sampling, repeated-run statistics, and monetary pricing remain future work.
Session-based personalization needs real approved cases and a task-specific evaluator.
Do not describe the public self-test as a real-session benchmark.

[^implementation]: `lib/src/skillz_experiments/_cli.py:14-73`; `skills/skillz/SKILL.md:33-52`; `skills/skillz/references/experiments.md:1-135`.
[^search]: `lib/src/skillz_experiments/_search.py:26-52`.
[^cases]: `lib/src/skillz_experiments/_cases.py:42-94`; `_records.py:28-50`.
[^candidate]: `lib/src/skillz_experiments/_candidate.py:14-51`; `_records.py:28-38`.
[^evaluator]: `lib/src/skillz_experiments/_evaluation.py:11-84`; `skills/skillz/scripts/inspect_skill.py:12-49`.
[^runtime]: `lib/src/skillz_experiments/_codex.py`; pinned source evidence in [Codex skill discovery and isolation](../sources/codex-skill-discovery-isolation.md).
[^workflow]: `lib/src/skillz_experiments/_workflow.py:35-202`; `_runtime.py:15-56`.
[^export]: `lib/src/skillz_experiments/_workflow.py:210-231`.

_Source: user-approved implementation scope, checked repository code, and primary-source research · Updated: 2026-09-28 · Supersedes: proposal-only status and unresolved first-harness choice_
